"""One process-wide writer gate for destructive archive restores.

Every diary writer may share this gate.  Normal writes serialize with a
restore, while a restore keeps the gate for its whole transaction.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
import weakref


class _LoopGate:
    def __init__(self) -> None:
        self.changed = asyncio.Condition()
        self.operations = 0
        self.exclusive = False


class MaintenanceGate:
    """A restore-exclusive gate: ordinary writes can proceed together."""

    def __init__(self) -> None:
        # AstrBot owns its plugin work on one loop.  Create that lock lazily:
        # tests can use separate short-lived loops without inheriting a lock
        # bound by the first one.
        self._locks: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
        self._restore_count = 0

    @property
    def restoring(self) -> bool:
        return self._restore_count > 0

    @asynccontextmanager
    async def operation(self):
        """Allow a normal write unless a restore owns or is waiting for the data."""
        gate = self._gate()
        async with gate.changed:
            await gate.changed.wait_for(lambda: not gate.exclusive)
            gate.operations += 1
        try:
            yield
        finally:
            async with gate.changed:
                gate.operations -= 1
                gate.changed.notify_all()

    @asynccontextmanager
    async def restore(self):
        """Reserve the gate for the full destructive restore transaction."""
        async with self._exclusive(restoring=True):
            yield

    @asynccontextmanager
    async def snapshot(self):
        """Freeze normal writes while a coherent archive snapshot is copied."""
        async with self._exclusive(restoring=False):
            yield

    @asynccontextmanager
    async def _exclusive(self, *, restoring: bool):
        gate = self._gate()
        async with gate.changed:
            await gate.changed.wait_for(lambda: not gate.exclusive)
            # Set this before waiting for in-flight writes so no later write
            # can enter between the drain check and destructive replacement.
            gate.exclusive = True
            await gate.changed.wait_for(lambda: gate.operations == 0)
            if restoring:
                self._restore_count += 1
        try:
            yield
        finally:
            async with gate.changed:
                if restoring:
                    self._restore_count -= 1
                gate.exclusive = False
                gate.changed.notify_all()

    def _gate(self) -> _LoopGate:
        loop = asyncio.get_running_loop()
        gate = self._locks.get(loop)
        if gate is None:
            gate = _LoopGate()
            self._locks[loop] = gate
        return gate


GLOBAL_MAINTENANCE_GATE = MaintenanceGate()

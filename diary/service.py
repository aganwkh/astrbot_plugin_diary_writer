from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import DiaryConfig
from .continuity import update_continuity
from .activity import classify_entry_type, select_historical_memories
from .events import cluster_memories
from .migration import migrate_legacy_markdown
from .memory_source import MemorySource
from .models import GenerationState, SourceMemory
from .maintenance import GLOBAL_MAINTENANCE_GATE, MaintenanceGate
from .prompts import build_adaptive_messages, build_messages, parse_diary_response
from .storage import DiaryStorage
from .website_sync import WebsiteSync

PROVIDER_TIMEOUT_SECONDS = 120


class DiaryGenerationResult(str):
    """Path-like result that records whether this call changed daily facts."""

    def __new__(cls, path: str, changed: bool):
        result = super().__new__(cls, path)
        result.changed = changed
        return result


def diary_changed(result: str | None) -> bool:
    """Treat legacy/mocked string results as changed while preserving real no-op calls."""
    return bool(result) and bool(getattr(result, "changed", True))


class DiaryService:
    def __init__(self, config: DiaryConfig, storage: DiaryStorage, source: MemorySource, gate: MaintenanceGate | None = None):
        self.config = config
        self.storage = storage
        self.source = source
        self.gate = gate or GLOBAL_MAINTENANCE_GATE
        self._locks: dict[str, asyncio.Lock] = {}

    async def generate(self, diary_date: date, provider: Any, force: bool = False) -> DiaryGenerationResult | None:
        """Write a daily only while no restore transaction owns the data root."""
        async with self.gate.operation():
            return await self._generate_unlocked(diary_date, provider, force)

    async def _generate_unlocked(self, diary_date: date, provider: Any, force: bool = False) -> DiaryGenerationResult | None:
        """Generate while the caller owns ``gate.operation()`` exactly once."""
        date_text = diary_date.isoformat()
        lock = self._locks.setdefault(date_text, asyncio.Lock())
        async with lock:
            return await self._generate_locked(diary_date, provider, force)

    async def _generate_locked(self, diary_date: date, provider: Any, force: bool) -> DiaryGenerationResult | None:
        date_text = diary_date.isoformat()
        if not force and self.storage.has_any_diary(date_text):
            changed = migrate_legacy_markdown(self.storage, date_text)
            return DiaryGenerationResult(str(self.storage.diary_path(date_text)), changed)
        state = GenerationState(pending_date=date_text, stage="collecting", updated_at=self._now())
        self.storage.save_generation_state(state)
        try:
            memories = self.source.read_day(diary_date)
            previous = self.storage.load_metadata(date_text) if force else None
            activity = self.storage.load_daily_activity(date_text)
            round_count = max(0, int(activity.get("round_count") or 0))
            entry_type = str(previous.get("entry_type") or "") if previous and previous.get("entry_type") == "low_activity" else classify_entry_type(
                round_count, len(memories), self.config.low_activity_round_threshold, self.config.sparse_memory_threshold,
            )
            events = cluster_memories(memories)
            conversation_sources: list[dict] = []
            recent_context_sources: list[dict] = []
            historical_sources: list[dict] = []
            sessions: list[str] = []
            if entry_type == "normal":
                system, prompt = build_messages(date_text, events, self.storage.load_continuity(), self.config)
            elif entry_type == "low_activity" and previous:
                conversation_sources = self._dict_list(previous.get("conversation_sources"))
                recent_context_sources = self._dict_list(previous.get("recent_context_sources"))
                historical_sources = self._dict_list(previous.get("historical_memory_sources"))
                sessions = self._strings(previous.get("private_session_ids"))
                system, prompt = build_adaptive_messages(date_text, entry_type, events, self.storage.load_continuity(), self.config, conversation_sources, recent_context_sources, historical_sources)
            else:
                conversation_sources = self._dict_list(activity.get("conversation_sources"))
                sessions = self._strings(activity.get("private_session_ids")) or self.storage.load_private_session_ids()
                recent = self._read_range(diary_date - timedelta(days=self.config.recent_context_days), diary_date - timedelta(days=1), set(sessions))
                recent_context_sources = [self._snapshot(item) for item in recent]
                if entry_type == "low_activity":
                    historical = select_historical_memories(
                        self._read_before(diary_date, set(sessions)), diary_date, self.storage.load_reflection_usage(), set(sessions),
                        minimum=self.config.historical_memory_min_count, maximum=self.config.historical_memory_max_count, cooldown_days=self.config.reflection_cooldown_days,
                    )
                    historical_sources = [self._snapshot(item) for item in historical]
                system, prompt = build_adaptive_messages(date_text, entry_type, events, self.storage.load_continuity(), self.config, conversation_sources, recent_context_sources, historical_sources)
            response = await self._call_provider(provider, system, prompt, state)
            parsed = parse_diary_response(response, date_text, {item.memory_id for item in memories}, {str(item.get("memory_id") or "") for item in historical_sources})
            parsed.metadata.provider = self.config.generation_provider_id
            parsed.metadata.model = self._provider_name(provider)
            parsed.metadata.entry_type = entry_type
            parsed.metadata.activity_round_count = round_count
            parsed.metadata.conversation_sources = conversation_sources
            parsed.metadata.recent_context_sources = recent_context_sources
            parsed.metadata.historical_memory_sources = historical_sources
            parsed.metadata.historical_memory_candidate_ids = [str(item.get("memory_id") or "") for item in historical_sources]
            parsed.metadata.historical_memory_used_ids = parsed.used_historical_memory_ids
            parsed.metadata.private_session_ids = sessions
            parsed.metadata.source_count = len(memories) + len(conversation_sources) + len(recent_context_sources) + len(historical_sources)
            self.storage.write_diary(date_text, parsed.markdown, parsed.metadata, backup_existing=force)
            self.storage.save_continuity(update_continuity(self.storage.load_continuity(), parsed.metadata))
            self._save_reflection_usage(parsed.used_historical_memory_ids)
            self.storage.delete_daily_activity(date_text)
            completed_at = self._now()
            self.storage.save_generation_state(GenerationState(retry_count=state.retry_count, last_success_at=completed_at, updated_at=completed_at))
            if self.config.website_sync_enabled and self.config.website_sync_path:
                try:
                    WebsiteSync(Path(self.config.website_sync_path)).sync(date_text, parsed.markdown, parsed.metadata)
                except Exception:
                    pass
            return DiaryGenerationResult(str(self.storage.diary_path(date_text)), True)
        except Exception as exc:
            state.stage = "failed"
            state.last_error = str(exc)[:1000]
            state.updated_at = self._now()
            self.storage.save_generation_state(state)
            return None

    async def preview(self, diary_date: date, provider: Any) -> str | None:
        """Generate an unsaved draft; never changes diary, state, continuity or sync."""
        try:
            memories = self.source.read_day(diary_date)
            if not memories: return None
            events = cluster_memories(memories)
            system, prompt = build_messages(diary_date.isoformat(), events, self.storage.load_continuity(), self.config)
            response = await self._call_provider_preview(provider, system, prompt)
            return parse_diary_response(response, diary_date.isoformat(), {item.memory_id for item in memories}).markdown
        except Exception:
            return None

    async def _call_provider_preview(self, provider: Any, system: str, prompt: str) -> str:
        response = await asyncio.wait_for(provider.text_chat(prompt=prompt, system_prompt=system, contexts=[]), timeout=PROVIDER_TIMEOUT_SECONDS)
        text = getattr(response, "completion_text", "")
        if not str(text).strip(): raise ValueError("provider returned an empty diary")
        return str(text)

    async def _call_provider(self, provider: Any, system: str, prompt: str, state: GenerationState) -> str:
        last_error: Exception | None = None
        for attempt in range(self.config.provider_retry_count + 1):
            state.stage = "generating"
            state.retry_count = attempt
            state.updated_at = self._now()
            self.storage.save_generation_state(state)
            try:
                response = await asyncio.wait_for(provider.text_chat(prompt=prompt, system_prompt=system, contexts=[]), timeout=PROVIDER_TIMEOUT_SECONDS)
                text = getattr(response, "completion_text", "")
                if not str(text).strip():
                    raise ValueError("provider returned an empty diary")
                return str(text)
            except Exception as exc:
                last_error = exc
                if attempt < self.config.provider_retry_count:
                    await asyncio.sleep(0.1 * (2**attempt))
        raise RuntimeError(str(last_error) if last_error else "provider failed")

    @staticmethod
    def _provider_name(provider: Any) -> str:
        try:
            metadata = provider.meta()
            return str(getattr(metadata, "id", ""))
        except Exception:
            return provider.__class__.__name__

    def _read_range(self, start: date, end: date, sessions: set[str]) -> list[SourceMemory]:
        reader = getattr(self.source, "read_range", None)
        return list(reader(start, end, sessions)) if callable(reader) and sessions else []

    def _read_before(self, before: date, sessions: set[str]) -> list[SourceMemory]:
        reader = getattr(self.source, "read_before", None)
        return list(reader(before, sessions)) if callable(reader) and sessions else []

    def _save_reflection_usage(self, memory_ids: list[str]) -> None:
        if not memory_ids:
            return
        usage = self.storage.load_reflection_usage()
        now = self._now()
        for memory_id in memory_ids:
            current = usage.get(memory_id, {})
            usage[memory_id] = {"last_reflected_at": now, "reflection_count": max(0, int(current.get("reflection_count") or 0)) + 1}
        self.storage.save_reflection_usage(usage)

    @staticmethod
    def _snapshot(memory: SourceMemory) -> dict[str, Any]:
        return {"memory_id": memory.memory_id, "occurred_at": memory.occurred_at.isoformat(), "text": memory.text, "importance": memory.importance, "session_id": memory.session_id}

    @staticmethod
    def _dict_list(value: Any) -> list[dict]:
        return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []

    @staticmethod
    def _strings(value: Any) -> list[str]:
        return list(dict.fromkeys(str(item) for item in value if str(item))) if isinstance(value, list) else []

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

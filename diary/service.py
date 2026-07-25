from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .config import DiaryConfig
from .continuity import update_continuity
from .events import cluster_memories
from .migration import migrate_legacy_markdown
from .memory_source import MemorySource, MemorySourceError
from .models import GenerationState
from .prompts import build_messages, parse_diary_response
from .storage import DiaryStorage
from .website_sync import WebsiteSync


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
    def __init__(self, config: DiaryConfig, storage: DiaryStorage, source: MemorySource):
        self.config = config
        self.storage = storage
        self.source = source
        self._locks: dict[str, asyncio.Lock] = {}

    async def generate(self, diary_date: date, provider: Any, force: bool = False) -> DiaryGenerationResult | None:
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
            if not memories:
                raise ValueError("no usable LivingMemory records for this date")
            events = cluster_memories(memories)
            system, prompt = build_messages(date_text, events, self.storage.load_continuity(), self.config)
            response = await self._call_provider(provider, system, prompt, state)
            parsed = parse_diary_response(response, date_text, {item.memory_id for item in memories})
            parsed.metadata.provider = self.config.generation_provider_id
            parsed.metadata.model = self._provider_name(provider)
            self.storage.write_diary(date_text, parsed.markdown, parsed.metadata, backup_existing=force)
            self.storage.save_continuity(update_continuity(self.storage.load_continuity(), parsed.metadata))
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
        response = await provider.text_chat(prompt=prompt, system_prompt=system, contexts=[])
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
                response = await provider.text_chat(prompt=prompt, system_prompt=system, contexts=[])
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

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

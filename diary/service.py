from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from .config import DiaryConfig
from .continuity import update_continuity
from .activity import classify_entry_type, select_historical_memories
from .events import cluster_memories
from .migration import migrate_legacy_markdown
from .memory_source import MemorySource
from .models import ContinuityState, DiaryEvent, DiaryMetadata, GenerationState, SourceMemory
from .maintenance import GLOBAL_MAINTENANCE_GATE, MaintenanceGate
from .prompts import ADAPTIVE_PROMPT_VERSION, NORMAL_PROMPT_VERSION, ParsedDiary, build_adaptive_messages, build_messages, parse_diary_response
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
    def __init__(
        self,
        config: DiaryConfig,
        storage: DiaryStorage,
        source: MemorySource,
        gate: MaintenanceGate | None = None,
        persona_resolver: Callable[[list[str]], Awaitable[str]] | None = None,
    ):
        self.config = config
        self.storage = storage
        self.source = source
        self.gate = gate or GLOBAL_MAINTENANCE_GATE
        self.persona_resolver = persona_resolver
        self._locks: dict[str, asyncio.Lock] = {}

    async def generate(self, diary_date: date, provider: Any, force: bool = False, persona_session_id: str = "") -> DiaryGenerationResult | None:
        """Write a daily only while no restore transaction owns the data root."""
        async with self.gate.operation():
            return await self._generate_unlocked(diary_date, provider, force, persona_session_id)

    async def _generate_unlocked(self, diary_date: date, provider: Any, force: bool = False, persona_session_id: str = "") -> DiaryGenerationResult | None:
        """Generate while the caller owns ``gate.operation()`` exactly once."""
        date_text = diary_date.isoformat()
        lock = self._locks.setdefault(date_text, asyncio.Lock())
        async with lock:
            return await self._generate_locked(diary_date, provider, force, persona_session_id)

    async def _generate_locked(self, diary_date: date, provider: Any, force: bool, persona_session_id: str = "") -> DiaryGenerationResult | None:
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
            activity_round_count = max(0, int(activity.get("round_count") or 0))
            previous_entry_type = str(previous.get("entry_type") or "") if previous else ""
            reuse_previous = bool(previous and previous_entry_type in {"normal", "low_activity"})
            if reuse_previous:
                entry_type = previous_entry_type
                round_count = max(0, int(previous.get("activity_round_count") or 0))
            else:
                entry_type = classify_entry_type(len(memories))
                round_count = max(0, int(previous.get("activity_round_count") or 0)) if previous else activity_round_count
            events = cluster_memories(memories)
            conversation_sources = self._dict_list(previous.get("conversation_sources")) if previous else self._dict_list(activity.get("conversation_sources"))
            recent_context_sources: list[dict] = []
            historical_sources: list[dict] = []
            sessions = self._strings(previous.get("private_session_ids")) if previous else (
                self._strings(activity.get("private_session_ids")) or self.storage.load_private_session_ids()
            )
            persona_prompt = await self._resolve_persona_prompt([persona_session_id] if persona_session_id else sessions)
            prompt_version = NORMAL_PROMPT_VERSION
            if entry_type == "normal":
                system, prompt = build_messages(date_text, events, ContinuityState(), self.config, conversation_sources, persona_prompt)
            elif entry_type == "low_activity" and reuse_previous:
                prompt_version = ADAPTIVE_PROMPT_VERSION
                recent_context_sources = self._dict_list(previous.get("recent_context_sources"))
                historical_sources = self._dict_list(previous.get("historical_memory_sources"))
                system, prompt = build_adaptive_messages(date_text, entry_type, events, ContinuityState(), self.config, conversation_sources, recent_context_sources, historical_sources, persona_prompt)
            else:
                prompt_version = ADAPTIVE_PROMPT_VERSION
                recent = self._read_range(diary_date - timedelta(days=self.config.recent_context_days), diary_date - timedelta(days=1), set(sessions))
                recent_context_sources = [self._snapshot(item) for item in recent]
                if entry_type == "low_activity":
                    historical = select_historical_memories(
                        self._read_before(diary_date, set(sessions)), diary_date, self.storage.load_reflection_usage(), set(sessions),
                        minimum=self.config.historical_memory_min_count, maximum=self.config.historical_memory_max_count, cooldown_days=self.config.reflection_cooldown_days,
                    )
                    historical_sources = [self._snapshot(item) for item in historical]
                system, prompt = build_adaptive_messages(date_text, entry_type, events, ContinuityState(), self.config, conversation_sources, recent_context_sources, historical_sources, persona_prompt)
            parsed = await self._call_and_parse(
                provider, system, prompt, state, date_text, {item.memory_id for item in memories},
                historical_sources, recent_context_sources, prompt_version, entry_type,
            )
            self._ensure_source_evidence(parsed.metadata, events)
            parsed.metadata.provider = self.config.generation_provider_id
            parsed.metadata.model = self._provider_name(provider)
            parsed.metadata.entry_type = entry_type
            parsed.metadata.activity_round_count = round_count
            parsed.metadata.conversation_sources = conversation_sources
            parsed.metadata.recent_context_sources = recent_context_sources
            parsed.metadata.recent_memory_used_ids = parsed.used_recent_memory_ids
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

    async def preview(self, diary_date: date, provider: Any, persona_session_id: str = "") -> str | None:
        """Generate an unsaved draft; never changes diary, state, continuity or sync."""
        try:
            memories = self.source.read_day(diary_date)
            if not memories: return None
            events = cluster_memories(memories)
            sessions = [persona_session_id] if persona_session_id else self.storage.load_private_session_ids()
            persona_prompt = await self._resolve_persona_prompt(sessions)
            system, prompt = build_messages(diary_date.isoformat(), events, ContinuityState(), self.config, astrbot_persona_prompt=persona_prompt)
            parsed = await self._call_and_parse(
                provider, system, prompt, None, diary_date.isoformat(), {item.memory_id for item in memories}, [], [], NORMAL_PROMPT_VERSION, "normal",
            )
            return parsed.markdown
        except Exception:
            return None

    async def _call_and_parse(
        self,
        provider: Any,
        system: str,
        prompt: str,
        state: GenerationState | None,
        date_text: str,
        allowed_memory_ids: set[str],
        historical_sources: list[dict],
        recent_sources: list[dict],
        prompt_version: str,
        entry_type: str,
    ) -> ParsedDiary:
        """Retry the complete provider contract, including JSON parsing and mode validation."""
        last_error: Exception | None = None
        for attempt in range(self.config.provider_retry_count + 1):
            if state is not None:
                state.stage = "generating"
                state.retry_count = attempt
                state.updated_at = self._now()
                self.storage.save_generation_state(state)
            try:
                response = await asyncio.wait_for(provider.text_chat(prompt=prompt, system_prompt=system, contexts=[]), timeout=PROVIDER_TIMEOUT_SECONDS)
                text = getattr(response, "completion_text", "")
                if not str(text).strip():
                    raise ValueError("provider returned an empty diary")
                parsed = parse_diary_response(
                    str(text), date_text, allowed_memory_ids,
                    {str(item.get("memory_id") or "") for item in historical_sources},
                    {str(item.get("memory_id") or "") for item in recent_sources},
                    prompt_version,
                )
                self._validate_mode_contract(entry_type, parsed, recent_sources, historical_sources)
                return parsed
            except Exception as exc:
                last_error = exc
                if attempt < self.config.provider_retry_count:
                    await asyncio.sleep(0.1 * (2**attempt))
        raise RuntimeError(str(last_error) if last_error else "provider failed")

    async def _resolve_persona_prompt(self, session_ids: list[str]) -> str:
        if self.persona_resolver is None:
            return ""
        return str(await self.persona_resolver(self._strings(session_ids)) or "").strip()

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
    def _validate_mode_contract(entry_type: str, parsed: Any, recent_sources: list[dict], historical_sources: list[dict]) -> None:
        """Reject adaptive output that does not declare the contract's minimum context use."""
        recent_candidates = {str(item.get("memory_id") or "") for item in recent_sources} - {""}
        historical_candidates = {str(item.get("memory_id") or "") for item in historical_sources} - {""}
        if entry_type == "sparse" and recent_candidates and not parsed.used_recent_memory_ids:
            raise ValueError("sparse diary did not use required recent context")
        if entry_type == "low_activity" and (recent_candidates or historical_candidates):
            if not parsed.used_recent_memory_ids and not parsed.used_historical_memory_ids:
                raise ValueError("low-activity diary did not use required context")

    @staticmethod
    def _ensure_source_evidence(metadata: DiaryMetadata, source_events: list[DiaryEvent]) -> None:
        """Fill only missing same-day evidence from deterministic source clusters."""
        covered = {memory_id for event in metadata.events for memory_id in event.memory_ids}
        for source in source_events:
            missing = [memory_id for memory_id in source.memory_ids if memory_id not in covered]
            if not missing:
                continue
            overlapping = next((event for event in metadata.events if set(event.memory_ids) & set(source.memory_ids)), None)
            if overlapping is not None:
                overlapping.memory_ids = list(dict.fromkeys(overlapping.memory_ids + missing))
                overlapping.facts = list(dict.fromkeys(overlapping.facts + source.facts))
                overlapping.topics = list(dict.fromkeys(overlapping.topics + source.topics))
                overlapping.time_range = list(dict.fromkeys(overlapping.time_range + source.time_range))[:2]
            else:
                metadata.events.append(DiaryEvent(
                    summary=source.summary,
                    memory_ids=missing,
                    kind=source.kind,
                    facts=list(source.facts),
                    inferences=list(source.inferences),
                    topics=list(source.topics),
                    time_range=list(source.time_range),
                ))
            covered.update(missing)
        metadata.memory_ids = list(dict.fromkeys(memory_id for event in metadata.events for memory_id in event.memory_ids))

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

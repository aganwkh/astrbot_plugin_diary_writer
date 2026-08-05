"""Subjective persona reflections kept separate from diary facts."""
from __future__ import annotations

import asyncio
import json
from calendar import monthrange
from datetime import date, datetime, timezone
from hashlib import sha256
from typing import Any, Awaitable, Callable

from .config import DiaryConfig
from .maintenance import GLOBAL_MAINTENANCE_GATE, MaintenanceGate
from .storage import DiaryStorage, atomic_write_json

PROVIDER_TIMEOUT_SECONDS = 120


def period_dates(kind: str, period: str) -> list[date]:
    if kind == "monthly":
        year, month = (int(value) for value in period.split("-", 1))
        return [date(year, month, day) for day in range(1, monthrange(year, month)[1] + 1)]
    if kind == "yearly":
        year = int(period)
        return [date(year, month, day) for month in range(1, 13) for day in range(1, monthrange(year, month)[1] + 1)]
    raise ValueError("reflection kind must be monthly or yearly")


def _canonical_event(event: Any) -> Any:
    if not isinstance(event, dict):
        return event
    result = {key: value for key, value in event.items() if key not in {"event_id", "fact_records"}}
    if not isinstance(result.get("facts"), list):
        result["facts"] = []
    if not result["facts"] and isinstance(event.get("fact_records"), list):
        result["facts"] = [str(item.get("value") or "") for item in event["fact_records"] if isinstance(item, dict) and str(item.get("value") or "")]
    return result


def core_fingerprint(metadata: dict[str, Any]) -> str:
    """Ignore storage-only IDs when checking whether a reflection source changed."""
    fields = ("title", "mood", "mood_score", "topics", "people", "projects", "events", "highlights", "unresolved", "ongoing_topics", "memory_ids")
    lists = {"topics", "people", "projects", "events", "highlights", "unresolved", "ongoing_topics", "memory_ids"}
    core = {field: ([] if field in lists and metadata.get(field) is None else ("" if field in {"title", "mood"} and metadata.get(field) is None else metadata.get(field))) for field in fields}
    if isinstance(core["events"], list):
        core["events"] = [_canonical_event(event) for event in core["events"]]
    return sha256(json.dumps(core, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


class ReflectionError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _source_refs(item: dict[str, Any]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Stable refs point into current facts; snapshots make old prompts auditable."""
    # Do not normalize/persist from this read-only derived writer.  Legacy daily
    # files without IDs retain field-level refs until safe repair adds IDs.
    normalized = item
    value_date = str(item.get("date") or "")
    refs: list[dict[str, str]] = []
    snapshots: list[dict[str, str]] = []
    for field in ("title", "mood", "topics", "people", "projects", "highlights", "unresolved", "ongoing_topics"):
        value = normalized.get(field)
        if isinstance(value, list):
            for text in value:
                if str(text).strip():
                    refs.append({"date": value_date, "field": field})
                    snapshots.append({"date": value_date, "field": field, "value": str(text).strip()})
        elif str(value or "").strip():
            refs.append({"date": value_date, "field": field})
            snapshots.append({"date": value_date, "field": field, "value": str(value).strip()})
    for event in normalized.get("events", []) if isinstance(normalized.get("events"), list) else []:
        if not isinstance(event, dict):
            continue
        event_id = str(event.get("event_id") or "")
        summary = str(event.get("summary") or "").strip()
        if summary:
            ref = {"date": value_date, "field": "events"}
            snapshot = {"date": value_date, "field": "events", "value": summary}
            if event_id:
                ref["event_id"] = event_id; snapshot["event_id"] = event_id
            refs.append(ref); snapshots.append(snapshot)
        for fact in event.get("fact_records", []) if isinstance(event.get("fact_records"), list) else []:
            if event_id and isinstance(fact, dict) and str(fact.get("fact_id") or "") and str(fact.get("value") or "").strip():
                refs.append({"date": value_date, "field": "events", "event_id": event_id, "fact_id": str(fact["fact_id"])})
                snapshots.append({"date": value_date, "field": "events", "event_id": event_id, "fact_id": str(fact["fact_id"]), "value": str(fact["value"]).strip()})
    return refs, snapshots


def mark_reflections_stale(storage: DiaryStorage, diary_date: str, reason: str) -> None:
    """A correction only stales reflections that explicitly cite its daily fact."""
    for kind, period, metadata in storage.iter_reflection_metadata() or ():
        refs = metadata.get("source_refs") if isinstance(metadata.get("source_refs"), list) else []
        if not any(isinstance(ref, dict) and str(ref.get("date") or "") == diary_date for ref in refs):
            continue
        metadata["reflection_stale"] = True
        metadata["stale_reason"] = f"{reason}:{diary_date}"
        metadata["stale_since"] = metadata.get("stale_since") or _now()
        atomic_write_json(storage.reflection_metadata_path(kind, period), metadata)


class ReflectionService:
    """Manual monthly/yearly reflection writer.  It never modifies daily data."""

    def __init__(
        self,
        storage: DiaryStorage,
        config: DiaryConfig | None = None,
        gate: MaintenanceGate | None = None,
        persona_resolver: Callable[[list[str]], Awaitable[str]] | None = None,
    ):
        self.storage = storage
        self.config = config or DiaryConfig()
        self.gate = gate or GLOBAL_MAINTENANCE_GATE
        self.persona_resolver = persona_resolver
        self._locks: dict[str, asyncio.Lock] = {}

    async def generate(self, kind: str, period: str, provider: Any, *, force: bool = False) -> str | None:
        if kind not in {"monthly", "yearly"}:
            raise ReflectionError("reflection kind must be monthly or yearly")
        async with self.gate.operation():
            key = f"{kind}:{period}"
            async with self._locks.setdefault(key, asyncio.Lock()):
                if not force and self.storage.has_reflection(kind, period):
                    return str(self.storage.reflection_path(kind, period))
                self._save_state(kind, period, "generating")
                try:
                    daily = [self.storage.load_metadata(item.isoformat()) for item in period_dates(kind, period)]
                    daily = [item for item in daily if item is not None]
                    if not daily:
                        raise ReflectionError("no daily facts in reflection period")
                    refs: list[dict[str, str]] = []
                    snapshots: list[dict[str, str]] = []
                    for item in daily:
                        item_refs, item_snapshots = _source_refs(item)
                        refs.extend(item_refs); snapshots.extend(item_snapshots)
                    persona_prompt = ""
                    if self.persona_resolver is not None:
                        persona_prompt = str(await self.persona_resolver(self.storage.load_private_session_ids()) or "").strip()
                    system_prompt = "Return JSON only with markdown and reflection. Write a subjective observation, not historical facts. Do not add facts beyond supplied snapshots."
                    if persona_prompt:
                        system_prompt = f"# AstrBot current persona\n\n{persona_prompt}\n\n# Reflection task\n\n{system_prompt}"
                    response = await asyncio.wait_for(
                        provider.text_chat(
                            prompt=json.dumps({"period": period, "facts": snapshots}, ensure_ascii=False),
                            system_prompt=system_prompt,
                            contexts=[],
                        ),
                        timeout=PROVIDER_TIMEOUT_SECONDS,
                    )
                    raw = str(getattr(response, "completion_text", "") or "").strip()
                    data = json.loads(raw.removeprefix("```json").removesuffix("```").strip())
                    markdown = str(data.get("markdown") or data.get("reflection") or "").strip()
                    if not markdown:
                        raise ReflectionError("provider response has no reflection")
                    metadata = {
                        "kind": kind, "period": period, "subjective": True,
                        "persona_source": "astrbot",
                        "reflection": str(data.get("reflection") or markdown), "source_dates": sorted({str(item.get("date")) for item in daily}),
                        "source_refs": refs, "source_facts": snapshots,
                        "source_fingerprints": {str(item["date"]): core_fingerprint(item) for item in daily},
                        "reflection_stale": False, "stale_reason": "", "stale_since": "", "generated_at": _now(),
                    }
                    self.storage.write_reflection(kind, period, markdown, metadata, backup_existing=force)
                    self._save_state(kind, period, "succeeded", last_success_at=_now())
                    return str(self.storage.reflection_path(kind, period))
                except Exception as exc:
                    self._save_state(kind, period, "failed", last_error=str(exc)[:1000])
                    return None

    def refresh_staleness(self, kind: str, period: str) -> None:
        metadata = self.storage.load_reflection_metadata(kind, period)
        if not metadata:
            return
        for value, fingerprint in (metadata.get("source_fingerprints") or {}).items():
            current = self.storage.load_metadata(str(value))
            if current is None or core_fingerprint(current) != fingerprint:
                mark_reflections_stale(self.storage, str(value), "daily_metadata_changed")
                return

    def _save_state(self, kind: str, period: str, stage: str, **extra: Any) -> None:
        state = self.storage._load_json(self.storage.reflection_state_path) or {}
        state[f"{kind}:{period}"] = {"kind": kind, "period": period, "stage": stage, "updated_at": _now(), **extra}
        atomic_write_json(self.storage.reflection_state_path, state)

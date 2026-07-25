from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
from typing import Any


@dataclass(frozen=True)
class SourceMemory:
    memory_id: str
    occurred_at: datetime
    text: str
    importance: float = 0.0
    session_id: str = ""
    topics: tuple[str, ...] = ()
    key_facts: tuple[str, ...] = ()


@dataclass
class DiaryEvent:
    summary: str
    memory_ids: list[str]
    kind: str = "event"
    facts: list[str] = field(default_factory=list)
    inferences: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    time_range: list[str] = field(default_factory=list)
    event_id: str = ""
    fact_records: list[dict[str, str]] = field(default_factory=list)


@dataclass
class DiaryMetadata:
    date: str
    title: str
    mood: str = ""
    mood_score: float | None = None
    topics: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    people: list[str] = field(default_factory=list)
    projects: list[str] = field(default_factory=list)
    events: list[DiaryEvent] = field(default_factory=list)
    highlights: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    ongoing_topics: list[str] = field(default_factory=list)
    memory_ids: list[str] = field(default_factory=list)
    source_count: int = 0
    generated_at: str = ""
    model: str = ""
    provider: str = ""
    prompt_version: str = "v1"


@dataclass
class ContinuityState:
    previous_summary: str = ""
    important_events: list[str] = field(default_factory=list)
    ongoing_projects: list[str] = field(default_factory=list)
    ongoing_topics: list[str] = field(default_factory=list)
    unresolved_items: list[str] = field(default_factory=list)
    recent_changes: list[str] = field(default_factory=list)


@dataclass
class GenerationState:
    pending_date: str = ""
    stage: str = "idle"
    retry_count: int = 0
    last_error: str = ""
    updated_at: str = ""
    last_success_at: str = ""


def as_jsonable(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return {key: as_jsonable(getattr(value, key)) for key in value.__dataclass_fields__}
    if isinstance(value, tuple):
        return [as_jsonable(item) for item in value]
    if isinstance(value, list):
        return [as_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): as_jsonable(item) for key, item in value.items()}
    return value


def normalize_daily_metadata(value: dict[str, Any]) -> dict[str, Any]:
    """Add stable event/fact IDs while retaining legacy ``facts`` strings."""
    result = {str(key): as_jsonable(item) for key, item in value.items()}
    date = str(result.get("date") or "")
    events = result.get("events")
    if not isinstance(events, list):
        return result
    normalized: list[Any] = []
    used_event_ids: set[str] = set()
    separator = "\x1f"
    for raw_event in events:
        if not isinstance(raw_event, dict):
            normalized.append(raw_event)
            continue
        event = {str(key): as_jsonable(item) for key, item in raw_event.items()}
        summary = str(event.get("summary") or "").strip()
        memory_ids = event.get("memory_ids") if isinstance(event.get("memory_ids"), list) else []
        seed = separator.join((date, summary, separator.join(str(item) for item in memory_ids)))
        event_id = str(event.get("event_id") or "").strip() or f"event_{sha256(seed.encode('utf-8')).hexdigest()[:16]}"
        duplicate = 1
        base_id = event_id
        while event_id in used_event_ids:
            duplicate += 1
            event_id = f"{base_id}_{duplicate}"
        used_event_ids.add(event_id)
        event["event_id"] = event_id
        facts = [str(item).strip() for item in event.get("facts", []) if str(item).strip()] if isinstance(event.get("facts"), list) else []
        records = event.get("fact_records") if isinstance(event.get("fact_records"), list) else []
        by_value: dict[str, list[dict[str, str]]] = {}
        for record in records:
            if not isinstance(record, dict):
                continue
            fact_value = str(record.get("value") or "").strip()
            fact_id = str(record.get("fact_id") or "").strip()
            if fact_value:
                by_value.setdefault(fact_value, []).append({"fact_id": fact_id, "value": fact_value})
        normalized_records: list[dict[str, str]] = []
        used_fact_ids: set[str] = set()
        for occurrence, fact in enumerate(facts, start=1):
            existing = (by_value.get(fact) or [])
            record = existing.pop(0) if existing else {}
            fact_seed = separator.join((event_id, fact, str(occurrence)))
            fact_id = str(record.get("fact_id") or "").strip() or f"fact_{sha256(fact_seed.encode('utf-8')).hexdigest()[:16]}"
            suffix = 1
            base_fact_id = fact_id
            while fact_id in used_fact_ids:
                suffix += 1
                fact_id = f"{base_fact_id}_{suffix}"
            used_fact_ids.add(fact_id)
            normalized_records.append({"fact_id": fact_id, "value": fact})
        event["facts"] = facts
        event["fact_records"] = normalized_records
        normalized.append(event)
    result["events"] = normalized
    return result

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
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

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from .storage import DiaryStorage


@dataclass(frozen=True)
class DiaryReference:
    date: str
    title: str
    summary: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class Timeline:
    first: DiaryReference
    latest: DiaryReference
    entries: list[DiaryReference]


def _reference(item: dict[str, Any]) -> DiaryReference:
    events = item.get("events") if isinstance(item.get("events"), list) else []
    first_event = next((str(event.get("summary") or "") for event in events if isinstance(event, dict) and event.get("summary")), "")
    summary = first_event or next(iter(item.get("highlights") or []), "") or str(item.get("title") or "")
    return DiaryReference(str(item.get("date") or ""), str(item.get("title") or ""), str(summary), item)


def _haystack(item: dict[str, Any]) -> str:
    event_text = [str(event.get(key) or "") for event in item.get("events", []) if isinstance(event, dict) for key in ("summary", "facts")]
    values = [item.get("date"), item.get("title"), *item.get("topics", []), *item.get("people", []), *item.get("projects", []), *item.get("highlights", []), *item.get("unresolved", []), *item.get("tags", []), *event_text]
    return " ".join(str(value) for value in values).casefold()


def search_daily(storage: DiaryStorage, query: str) -> list[DiaryReference]:
    terms = [term.casefold() for term in query.split() if term.strip()]
    return [_reference(item) for item in storage.iter_daily_metadata() if all(term in _haystack(item) for term in terms)]


def on_this_day(storage: DiaryStorage, today: date) -> list[DiaryReference]:
    results = []
    for item in storage.iter_daily_metadata():
        try:
            value = date.fromisoformat(str(item.get("date")))
        except ValueError:
            continue
        if value.year < today.year and (value.month, value.day) == (today.month, today.day):
            results.append(_reference(item))
    return sorted(results, key=lambda item: item.date, reverse=True)


def timeline(storage: DiaryStorage, value: str, field: str) -> Timeline | None:
    needle = value.casefold()
    entries = [_reference(item) for item in storage.iter_daily_metadata() if any(str(item_value).casefold() == needle for item_value in item.get(field, []))]
    entries.sort(key=lambda item: item.date)
    return Timeline(entries[0], entries[-1], entries) if entries else None

"""Read-only people and project lifecycles derived from current daily facts."""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from .storage import DiaryStorage


_FIELDS = {"people", "projects"}


def _values(item: dict[str, Any], field: str) -> list[str]:
    raw = item.get(field)
    if not isinstance(raw, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for value in raw:
        text = str(value).strip() if isinstance(value, str) else ""
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return result


def _event_rows(item: dict[str, Any], field: str, needle: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for event in item.get("events", []) if isinstance(item.get("events"), list) else []:
        if not isinstance(event, dict) or not str(event.get("summary") or "").strip():
            continue
        direct = event.get(field)
        direct_match = isinstance(direct, list) and any(str(value).casefold() == needle for value in direct)
        text = " ".join(
            [str(event.get("summary") or ""), *(str(value) for value in event.get("facts", []) if isinstance(event.get("facts"), list)), *(str(value) for value in event.get("topics", []) if isinstance(event.get("topics"), list))]
        ).casefold()
        if direct_match or needle in text:
            rows.append({"date": str(item.get("date") or ""), "event_id": str(event.get("event_id") or ""), "summary": str(event["summary"]).strip()})
    return rows


def _intervals(values: list[date]) -> list[dict[str, str]]:
    if not values:
        return []
    result: list[dict[str, str]] = []
    start = previous = values[0]
    for current in values[1:]:
        if (current - previous).days == 1:
            previous = current
            continue
        result.append({"start": start.isoformat(), "end": previous.isoformat()})
        start = previous = current
    result.append({"start": start.isoformat(), "end": previous.isoformat()})
    return result


def lifecycle(storage: DiaryStorage, value: str, field: str, *, as_of: str | date | None = None) -> dict[str, Any] | None:
    """Describe only recorded appearances; it never assigns a completion state."""
    if field not in _FIELDS:
        raise ValueError("field must be people or projects")
    target = str(value).strip()
    if not target:
        raise ValueError("value is required")
    needle = target.casefold()
    seen: list[tuple[date, dict[str, Any], str, int]] = []
    for item in storage.iter_daily_metadata() or ():
        try:
            item_date = date.fromisoformat(str(item.get("date") or ""))
        except ValueError:
            continue
        raw = item.get(field) if isinstance(item.get(field), list) else []
        matches = [str(text).strip() for text in raw if isinstance(text, str) and str(text).strip().casefold() == needle]
        if matches:
            seen.append((item_date, item, matches[0], len(matches)))
    if not seen:
        return None
    seen.sort(key=lambda row: row[0])
    dates = [row[0] for row in seen]
    display = seen[0][2]
    monthly: dict[str, int] = defaultdict(int)
    events: list[dict[str, str]] = []
    for current, item, _, _ in seen:
        monthly[current.strftime("%Y-%m")] += 1
        events.extend(_event_rows(item, field, needle))
    try:
        observed_at = date.fromisoformat(as_of) if isinstance(as_of, str) else as_of
    except ValueError as exc:
        raise ValueError("as_of must be an ISO date") from exc
    if observed_at is None:
        observed_at = date.today()
    # "recent_not_observed" is deliberately descriptive, not a claim of ending.
    age_days = (observed_at - dates[-1]).days
    status = "unknown" if observed_at < dates[0] else ("active" if age_days <= 31 else "recent_not_observed")
    return {
        "field": field,
        "name": display,
        "first_seen": dates[0].isoformat(),
        "last_seen": dates[-1].isoformat(),
        "occurrence_count": sum(row[3] for row in seen),
        "coverage_count": len(dates),
        "coverage_dates": [item.isoformat() for item in dates],
        "monthly_activity": [{"month": month, "coverage_count": count} for month, count in sorted(monthly.items())],
        "key_related_events": events,
        "continuous_intervals": _intervals(dates),
        "observational_status": status if dates else "unknown",
    }

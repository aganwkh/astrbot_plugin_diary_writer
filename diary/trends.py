"""Read-only descriptive trends derived from daily diary metadata."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
from math import isfinite
from typing import Any

from .storage import DiaryStorage


def _bound(value: str | date | None, name: str) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as error:
            raise ValueError(f"{name} must be an ISO date") from error
    raise ValueError(f"{name} must be an ISO date")


def _values(item: dict[str, Any], field: str) -> list[str]:
    raw = item.get(field)
    if not isinstance(raw, list):
        return []
    seen: set[str] = set()
    result: list[str] = []
    for value in raw:
        text = str(value).strip() if isinstance(value, str) else ""
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return result


def _ranked(counter: Counter[str], names: dict[str, str]) -> list[dict[str, Any]]:
    return [{"name": names[key], "count": count} for key, count in sorted(counter.items(), key=lambda entry: (-entry[1], names[entry[0]].casefold()))]


def build_trends(storage: DiaryStorage, start: str | date | None = None, end: str | date | None = None) -> dict[str, Any]:
    """Aggregate daily facts only; no generated data is written back to storage."""
    start_date, end_date = _bound(start, "start"), _bound(end, "end")
    if start_date and end_date and start_date > end_date:
        raise ValueError("start must not be after end")

    mood_points: list[dict[str, Any]] = []
    mood_categories: list[dict[str, str]] = []
    mood_counts: Counter[str] = Counter()
    mood_names: dict[str, str] = {}
    monthly: dict[str, dict[str, Any]] = {}
    topic_counts: Counter[str] = Counter()
    project_counts: Counter[str] = Counter()
    topic_names: dict[str, str] = {}
    project_names: dict[str, str] = {}
    projects_by_month: dict[str, dict[str, str]] = defaultdict(dict)

    for item in storage.iter_daily_metadata():
        try:
            item_date = date.fromisoformat(str(item.get("date") or ""))
        except ValueError:
            continue
        if (start_date and item_date < start_date) or (end_date and item_date > end_date):
            continue
        month = item_date.strftime("%Y-%m")
        bucket = monthly.setdefault(month, {"month": month, "diary_count": 0, "event_count": 0, "unresolved_count": 0, "_moods": []})
        bucket["diary_count"] += 1
        bucket["event_count"] += sum(isinstance(event, dict) for event in item.get("events", [])) if isinstance(item.get("events"), list) else 0
        bucket["unresolved_count"] += len(item["unresolved"]) if isinstance(item.get("unresolved"), list) else 0

        score = item.get("mood_score")
        if isinstance(score, (int, float)) and not isinstance(score, bool) and isfinite(score):
            numeric_score = float(score)
            mood_points.append({"date": item_date.isoformat(), "mood_score": numeric_score})
            bucket["_moods"].append(numeric_score)

        mood = item.get("mood")
        if isinstance(mood, str) and (mood := mood.strip()):
            key = mood.casefold()
            mood_categories.append({"date": item_date.isoformat(), "mood": mood})
            mood_counts[key] += 1
            mood_names.setdefault(key, mood)

        for value in _values(item, "topics"):
            key = value.casefold()
            topic_counts[key] += 1
            topic_names.setdefault(key, value)
        for value in _values(item, "projects"):
            key = value.casefold()
            project_counts[key] += 1
            project_names.setdefault(key, value)
            projects_by_month[month].setdefault(key, value)

    monthly_rows = []
    for month, bucket in sorted(monthly.items()):
        moods = bucket.pop("_moods")
        monthly_rows.append({**bucket, "mood_score_average": (sum(moods) / len(moods)) if moods else None})

    previous: set[str] = set()
    activity = []
    for month in sorted(monthly):
        observed = projects_by_month[month]
        current = set(observed)
        activity.append({
            "month": month,
            "observed": [observed[key] for key in sorted(current, key=lambda key: observed[key].casefold())],
            "added": [observed[key] for key in sorted(current - previous, key=lambda key: observed[key].casefold())],
            "absent": [project_names[key] for key in sorted(previous - current, key=lambda key: project_names[key].casefold())],
        })
        previous = current

    return {
        "start": start_date.isoformat() if start_date else None,
        "end": end_date.isoformat() if end_date else None,
        "mood_points": mood_points,
        "mood_categories": mood_categories,
        "mood_counts": [{"mood": mood_names[key], "count": count} for key, count in sorted(mood_counts.items(), key=lambda entry: (-entry[1], mood_names[entry[0]].casefold()))],
        "monthly": monthly_rows,
        "topics": _ranked(topic_counts, topic_names),
        "projects": _ranked(project_counts, project_names),
        "project_activity": activity,
    }

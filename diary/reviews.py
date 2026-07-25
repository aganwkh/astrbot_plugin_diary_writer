from __future__ import annotations

import asyncio
import json
from calendar import monthrange
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from math import isfinite
from typing import Any

from .config import DiaryConfig
from .maintenance import GLOBAL_MAINTENANCE_GATE, MaintenanceGate
from .storage import DiaryStorage, atomic_write_json


CORE_FIELDS = ("title", "mood", "mood_score", "topics", "people", "projects", "events", "highlights", "unresolved", "ongoing_topics", "memory_ids")
REVIEW_CORE_FIELDS = ("title", "topics", "people", "projects", "events", "highlights", "unresolved")
ANNUAL_PROMPT_DAILY_LIMIT = 120
ANNUAL_PROMPT_EVENT_LIMIT = 3


def period_dates(kind: str, period: str) -> list[date]:
    if kind == "weekly":
        year, week = period.split("-W", 1)
        start = date.fromisocalendar(int(year), int(week), 1)
        return [start + timedelta(days=offset) for offset in range(7)]
    if kind == "monthly":
        year, month = (int(value) for value in period.split("-", 1))
        return [date(year, month, day) for day in range(1, monthrange(year, month)[1] + 1)]
    if kind == "yearly":
        year = int(period)
        return [date(year, month, day) for month in range(1, 13) for day in range(1, monthrange(year, month)[1] + 1)]
    raise ValueError("review kind must be weekly, monthly, or yearly")


def period_for_date(kind: str, value: date) -> str:
    if kind == "weekly":
        return f"{value.isocalendar().year}-W{value.isocalendar().week:02d}"
    if kind == "monthly":
        return value.strftime("%Y-%m")
    if kind == "yearly":
        return value.strftime("%Y")
    raise ValueError("review kind must be weekly, monthly, or yearly")


def _canonical_event(event: Any) -> Any:
    """IDs and fact-record mirrors are technical storage, not changed facts."""
    if not isinstance(event, dict):
        return event
    result = {key: value for key, value in event.items() if key not in {"event_id", "fact_records"}}
    facts = result.get("facts")
    if not isinstance(facts, list):
        facts = []
    if not facts and isinstance(event.get("fact_records"), list):
        facts = [str(item.get("value") or "") for item in event["fact_records"] if isinstance(item, dict) and str(item.get("value") or "")]
    result["facts"] = facts
    return result


def core_fingerprint(metadata: dict[str, Any]) -> str:
    list_fields = {"topics", "people", "projects", "events", "highlights", "unresolved", "ongoing_topics", "memory_ids"}
    core = {
        field: ([] if field in list_fields and metadata.get(field) is None else ("" if field in {"title", "mood"} and metadata.get(field) is None else metadata.get(field)))
        for field in CORE_FIELDS
    }
    core["events"] = [_canonical_event(item) for item in core.get("events", [])] if isinstance(core.get("events"), list) else core.get("events")
    return sha256(json.dumps(core, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def review_fingerprint(metadata: dict[str, Any]) -> str:
    core = {field: metadata.get(field) for field in REVIEW_CORE_FIELDS}
    return sha256(json.dumps(core, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


class ReviewService:
    def __init__(self, storage: DiaryStorage, config: DiaryConfig | None = None, gate: MaintenanceGate | None = None):
        self.storage = storage
        self.config = config or DiaryConfig()
        self.gate = gate or GLOBAL_MAINTENANCE_GATE
        self._locks: dict[str, asyncio.Lock] = {}

    def collect(self, kind: str, period: str) -> tuple[list[dict[str, Any]], list[str], list[str]]:
        dates = [item.isoformat() for item in period_dates(kind, period)]
        daily = [self.storage.load_metadata(value) for value in dates]
        covered = [item for item in daily if item is not None]
        return covered, [str(item["date"]) for item in covered], [value for value, item in zip(dates, daily) if item is None]

    async def generate(self, kind: str, period: str, provider: Any, force: bool = False) -> str | None:
        """Write a review only while no archive restore owns the data root."""
        async with self.gate.operation():
            return await self._generate_unlocked(kind, period, provider, force)

    async def _generate_unlocked(self, kind: str, period: str, provider: Any, force: bool = False) -> str | None:
        key = f"{kind}:{period}"
        async with self._locks.setdefault(key, asyncio.Lock()):
            if not force and self.storage.has_review(kind, period):
                return str(self.storage.review_path(kind, period))
            self._save_state(kind, period, "collecting")
            try:
                previous = self.storage.load_review_metadata(kind, period)
                daily, covered_dates, missing_dates = self.collect(kind, period)
                if not daily:
                    raise ValueError("no daily metadata in review period")
                monthly_context = self._monthly_context(period) if kind == "yearly" else []
                response = await self._call_provider(provider, kind, period, daily, monthly_context)
                markdown, metadata = self._parse_response(response, kind, period, daily, covered_dates, missing_dates, monthly_context, provider)
                self.storage.write_review(kind, period, markdown, metadata, backup_existing=force)
                # Sources may have changed while the provider call was in flight.
                self.refresh_staleness(kind, period)
                if kind == "monthly" and previous and review_fingerprint(previous) != review_fingerprint(metadata):
                    self._mark_monthly_changed(period)
                self._save_state(kind, period, "succeeded", last_success_at=self._now())
                return str(self.storage.review_path(kind, period))
            except Exception as exc:
                self._save_state(kind, period, "failed", last_error=str(exc)[:1000])
                return None

    async def after_daily_written(self, diary_date: date, provider: Any, reason: str) -> None:
        """Update derived review state as one restore-safe operation."""
        async with self.gate.operation():
            await self._after_daily_written_unlocked(diary_date, provider, reason)

    async def _after_daily_written_unlocked(self, diary_date: date, provider: Any, reason: str) -> None:
        self.mark_daily_changed(diary_date, reason)
        today = date.today()
        for kind in ("weekly", "monthly", "yearly"):
            period = period_for_date(kind, diary_date)
            dates = period_dates(kind, period)
            if dates[-1] == diary_date and dates[-1] < today and not self.storage.has_review(kind, period):
                await self._generate_unlocked(kind, period, provider)

    async def catch_up(self, provider: Any) -> None:
        """Run the stale scan and any catch-up writes under one gate acquisition."""
        async with self.gate.operation():
            await self._catch_up_unlocked(provider)

    async def _catch_up_unlocked(self, provider: Any) -> None:
        periods = {(kind, period_for_date(kind, date.fromisoformat(str(item["date"])))) for item in self.storage.iter_daily_metadata() for kind in ("weekly", "monthly", "yearly")}
        for kind, period in sorted(periods):
            if period_dates(kind, period)[-1] >= date.today():
                continue
            self.refresh_staleness(kind, period)
            if not self.storage.has_review(kind, period):
                await self._generate_unlocked(kind, period, provider)

    def mark_daily_changed(self, diary_date: date, reason: str, core_changed: bool = True) -> None:
        if not core_changed:
            return
        value = diary_date.isoformat()
        for kind, period, metadata in self.storage.iter_review_metadata() or ():
            if value not in {day.isoformat() for day in period_dates(kind, period)}:
                continue
            metadata["summary_stale"] = True
            metadata["stale_reason"] = f"{reason}:{value}"
            metadata["stale_since"] = metadata.get("stale_since") or self._now()
            atomic_write_json(self.storage.review_metadata_path(kind, period), metadata)

    def _mark_monthly_changed(self, monthly_period: str) -> None:
        current = self.storage.load_review_metadata("monthly", monthly_period)
        if current is None:
            return
        fingerprint = review_fingerprint(current)
        for kind, period, metadata in self.storage.iter_review_metadata() or ():
            if kind != "yearly" or metadata.get("source_monthly_fingerprints", {}).get(monthly_period) == fingerprint:
                continue
            if monthly_period not in metadata.get("source_monthly_fingerprints", {}):
                continue
            metadata["summary_stale"] = True
            metadata["stale_reason"] = f"monthly_review_changed:{monthly_period}"
            metadata["stale_since"] = metadata.get("stale_since") or self._now()
            atomic_write_json(self.storage.review_metadata_path(kind, period), metadata)

    def refresh_staleness(self, kind: str, period: str) -> None:
        metadata = self.storage.load_review_metadata(kind, period)
        if not metadata:
            return
        fingerprints = metadata.get("source_fingerprints", {})
        for value, fingerprint in fingerprints.items():
            daily = self.storage.load_metadata(value)
            if daily is None:
                metadata["summary_stale"] = True
                metadata["stale_reason"] = f"daily_metadata_deleted:{value}"
                metadata["stale_since"] = metadata.get("stale_since") or self._now()
                atomic_write_json(self.storage.review_metadata_path(kind, period), metadata)
                return
            if fingerprint != core_fingerprint(daily):
                self.mark_daily_changed(date.fromisoformat(value), "daily_metadata_changed")
                return
        if kind == "yearly":
            current_monthly = {item["period"]: item["fingerprint"] for item in self._monthly_context(period)}
            for source_period, fingerprint in metadata.get("source_monthly_fingerprints", {}).items():
                if current_monthly.get(source_period) != fingerprint:
                    metadata["summary_stale"] = True
                    metadata["stale_reason"] = f"monthly_review_changed:{source_period}"
                    metadata["stale_since"] = metadata.get("stale_since") or self._now()
                    atomic_write_json(self.storage.review_metadata_path(kind, period), metadata)
                    return

    async def _call_provider(self, provider: Any, kind: str, period: str, daily: list[dict[str, Any]], monthly_context: list[dict[str, Any]] | None = None) -> str:
        material = self._annual_prompt_material(daily) if kind == "yearly" else [{key: item.get(key, []) for key in ("date", "title", "mood", "mood_score", "topics", "people", "projects", "events", "highlights", "unresolved", "ongoing_topics")} for item in daily]
        prompt_data = {"kind": kind, "period": period, "daily": material, "monthly_context": monthly_context or []}
        if kind == "yearly":
            prompt_data.update({"daily_source_dates": [str(item["date"]) for item in daily], "daily_aggregates": self._daily_facts(daily)["aggregates"]})
        prompt = json.dumps(prompt_data, ensure_ascii=False)
        system = "Return JSON only: markdown,title,topics,people,projects,events,highlights,unresolved. Events require summary,source_dates,facts,inferences. Use only supplied daily facts and dates."
        if kind == "yearly":
            system += " Daily is the sole source for event, topic, project, mood, and all numerical facts. monthly_context is labelled high-level context only; do not combine it with daily for counts or duplicate its events."
        last_error = None
        for _attempt in range(self.config.provider_retry_count + 1):
            try:
                result = await provider.text_chat(prompt=prompt, system_prompt=system, contexts=[])
                text = str(getattr(result, "completion_text", "") or "").strip()
                if text:
                    return text
                raise ValueError("provider returned empty review")
            except Exception as exc:
                last_error = exc
        raise RuntimeError(str(last_error))

    def _parse_response(self, raw: str, kind: str, period: str, daily: list[dict[str, Any]], covered_dates: list[str], missing_dates: list[str], monthly_context: list[dict[str, Any]], provider: Any) -> tuple[str, dict[str, Any]]:
        data = json.loads(raw.strip().removeprefix("```json").removesuffix("```").strip())
        if not isinstance(data, dict) or not str(data.get("markdown") or "").strip():
            raise ValueError("provider response has no review markdown")
        allowed = set(covered_dates)
        events = []
        for item in data.get("events", []):
            if not isinstance(item, dict):
                continue
            sources = [str(value) for value in item.get("source_dates", []) if str(value) in allowed]
            if sources:
                events.append({"summary": str(item.get("summary") or "")[:500], "source_dates": sources, "facts": [str(value) for value in item.get("facts", []) if str(value)], "inferences": [str(value) for value in item.get("inferences", []) if str(value)]})
        dates = period_dates(kind, period)
        metadata = {
            "kind": kind, "period": period, "start_date": dates[0].isoformat(), "end_date": dates[-1].isoformat(),
            "title": str(data.get("title") or period)[:200], "covered_dates": covered_dates, "missing_dates": missing_dates,
            "source_diary_dates": covered_dates, "source_fingerprints": {str(item["date"]): core_fingerprint(item) for item in daily},
            "summary_stale": False, "stale_reason": "", "stale_since": "", "events": events,
            "topics": self._strings(data.get("topics")), "people": self._strings(data.get("people")), "projects": self._strings(data.get("projects")),
            "highlights": self._strings(data.get("highlights")), "unresolved": self._strings(data.get("unresolved")),
            "generated_at": self._now(), "provider": self.config.generation_provider_id, "model": provider.__class__.__name__, "prompt_version": "review-v1",
        }
        if kind == "yearly":
            facts = self._daily_facts(daily)
            months = [f"{period}-{month:02d}" for month in range(1, 13)]
            covered_periods = sorted({value[:7] for value in covered_dates})
            metadata.update({
                "events": facts["events"], "topics": facts["topics"], "people": facts["people"], "projects": facts["projects"],
                "highlights": facts["highlights"], "unresolved": facts["unresolved"], "fact_aggregates": facts["aggregates"],
                "covered_periods": covered_periods,
                "missing_periods": [value for value in months if value not in covered_periods],
                "source_monthly_periods": [item["period"] for item in monthly_context],
                "source_monthly_fingerprints": {item["period"]: item["fingerprint"] for item in monthly_context},
            })
        return str(data["markdown"]).strip() + "\n", metadata

    def _daily_facts(self, daily: list[dict[str, Any]]) -> dict[str, Any]:
        values = {field: [] for field in ("topics", "people", "projects", "highlights", "unresolved")}
        topic_counts: Counter[str] = Counter()
        project_counts: Counter[str] = Counter()
        mood_counts: Counter[str] = Counter()
        scores: list[float] = []
        events = []
        for item in daily:
            for field in values:
                current = self._strings(item.get(field))
                values[field].extend(value for value in current if value not in values[field])
            topic_counts.update(self._strings(item.get("topics")))
            project_counts.update(self._strings(item.get("projects")))
            mood = str(item.get("mood") or "").strip()
            if mood:
                mood_counts[mood] += 1
            score = item.get("mood_score")
            if isinstance(score, (int, float)) and not isinstance(score, bool) and isfinite(score):
                scores.append(float(score))
            source_events = item.get("events")
            if not isinstance(source_events, list):
                continue
            for event in source_events:
                if not isinstance(event, dict):
                    continue
                summary = str(event.get("summary") or "").strip()
                if not summary:
                    continue
                events.append({
                    "summary": summary[:500], "source_dates": [str(item["date"])],
                    "facts": self._strings(event.get("facts")), "inferences": self._strings(event.get("inferences")),
                    "memory_ids": self._strings(event.get("memory_ids")),
                })
        return {
            **values, "events": events,
            "aggregates": {
                "diary_count": len(daily), "event_count": len(events),
                "topic_counts": dict(sorted(topic_counts.items())), "project_counts": dict(sorted(project_counts.items())),
                "mood_counts": dict(sorted(mood_counts.items())),
                "mood_score": {"count": len(scores), "average": sum(scores) / len(scores)} if scores else {"count": 0, "average": None},
            },
        }

    def _annual_prompt_material(self, daily: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ranked = sorted(daily, key=lambda item: (-self._daily_signal(item), str(item.get("date") or "")))[:ANNUAL_PROMPT_DAILY_LIMIT]
        material = []
        for item in sorted(ranked, key=lambda value: str(value.get("date") or "")):
            events = item.get("events") if isinstance(item.get("events"), list) else []
            material.append({
                "date": str(item.get("date") or ""), "title": str(item.get("title") or "")[:200],
                "mood": str(item.get("mood") or ""), "mood_score": self._finite_score(item.get("mood_score")),
                "topics": self._strings(item.get("topics"))[:12], "people": self._strings(item.get("people"))[:12],
                "projects": self._strings(item.get("projects"))[:12], "highlights": self._strings(item.get("highlights"))[:6],
                "unresolved": self._strings(item.get("unresolved"))[:6], "event_count": len(events),
                "events": [{"summary": str(event.get("summary") or "")[:300], "memory_ids": self._strings(event.get("memory_ids"))[:8]} for event in events[:ANNUAL_PROMPT_EVENT_LIMIT] if isinstance(event, dict)],
            })
        return material

    @staticmethod
    def _finite_score(value: Any) -> float | None:
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value) else None

    @staticmethod
    def _daily_signal(item: dict[str, Any]) -> int:
        return sum(len(item.get(field) or []) for field in ("events", "highlights", "unresolved", "topics", "projects"))

    def _monthly_context(self, year: str) -> list[dict[str, Any]]:
        context = []
        for month in range(1, 13):
            period = f"{year}-{month:02d}"
            if not self.storage.has_review("monthly", period):
                continue
            metadata = self.storage.load_review_metadata("monthly", period)
            if metadata is None or metadata.get("summary_stale"):
                continue
            context.append({
                "period": period,
                "title": str(metadata.get("title") or period),
                "highlights": self._strings(metadata.get("highlights")),
                "unresolved": self._strings(metadata.get("unresolved")),
                "topics": self._strings(metadata.get("topics")),
                "projects": self._strings(metadata.get("projects")),
                "fingerprint": review_fingerprint(metadata),
            })
        return context

    def _save_state(self, kind: str, period: str, stage: str, **extra: str) -> None:
        try:
            state = json.loads(self.storage.review_state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state = {}
        entries = state.get("entries") if isinstance(state.get("entries"), dict) else {}
        now = self._now()
        if kind and period:
            entries[f"{kind}:{period}"] = {"kind": kind, "pending_period": period, "stage": stage, "updated_at": now, **extra}
        state.update({"kind": kind, "pending_period": period, "stage": "idle" if stage == "succeeded" else stage, "updated_at": now, "entries": entries, **extra})
        atomic_write_json(self.storage.review_state_path, state)

    @staticmethod
    def _strings(value: Any) -> list[str]:
        return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip())) if isinstance(value, list) else []

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

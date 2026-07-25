from __future__ import annotations

import asyncio
import json
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from typing import Any

from .config import DiaryConfig
from .storage import DiaryStorage, atomic_write_json


CORE_FIELDS = ("title", "mood", "mood_score", "topics", "people", "projects", "events", "highlights", "unresolved", "ongoing_topics", "memory_ids")


def period_dates(kind: str, period: str) -> list[date]:
    if kind == "weekly":
        year, week = period.split("-W", 1)
        start = date.fromisocalendar(int(year), int(week), 1)
        return [start + timedelta(days=offset) for offset in range(7)]
    if kind == "monthly":
        year, month = (int(value) for value in period.split("-", 1))
        return [date(year, month, day) for day in range(1, monthrange(year, month)[1] + 1)]
    raise ValueError("review kind must be weekly or monthly")


def period_for_date(kind: str, value: date) -> str:
    return f"{value.isocalendar().year}-W{value.isocalendar().week:02d}" if kind == "weekly" else value.strftime("%Y-%m")


def core_fingerprint(metadata: dict[str, Any]) -> str:
    core = {field: metadata.get(field) for field in CORE_FIELDS}
    return sha256(json.dumps(core, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


class ReviewService:
    def __init__(self, storage: DiaryStorage, config: DiaryConfig | None = None):
        self.storage = storage
        self.config = config or DiaryConfig()
        self._locks: dict[str, asyncio.Lock] = {}

    def collect(self, kind: str, period: str) -> tuple[list[dict[str, Any]], list[str], list[str]]:
        dates = [item.isoformat() for item in period_dates(kind, period)]
        daily = [self.storage.load_metadata(value) for value in dates]
        covered = [item for item in daily if item is not None]
        return covered, [str(item["date"]) for item in covered], [value for value, item in zip(dates, daily) if item is None]

    async def generate(self, kind: str, period: str, provider: Any, force: bool = False) -> str | None:
        key = f"{kind}:{period}"
        async with self._locks.setdefault(key, asyncio.Lock()):
            if not force and self.storage.has_review(kind, period):
                return str(self.storage.review_path(kind, period))
            self._save_state(kind, period, "collecting")
            try:
                daily, covered_dates, missing_dates = self.collect(kind, period)
                if not daily:
                    raise ValueError("no daily metadata in review period")
                response = await self._call_provider(provider, kind, period, daily)
                markdown, metadata = self._parse_response(response, kind, period, daily, covered_dates, missing_dates, provider)
                self.storage.write_review(kind, period, markdown, metadata, backup_existing=force)
                self._save_state("", "", "idle", last_success_at=self._now())
                return str(self.storage.review_path(kind, period))
            except Exception as exc:
                self._save_state(kind, period, "failed", last_error=str(exc)[:1000])
                return None

    async def after_daily_written(self, diary_date: date, provider: Any, reason: str) -> None:
        self.mark_daily_changed(diary_date, reason)
        today = date.today()
        for kind in ("weekly", "monthly"):
            period = period_for_date(kind, diary_date)
            dates = period_dates(kind, period)
            if dates[-1] == diary_date and dates[-1] < today and not self.storage.has_review(kind, period):
                await self.generate(kind, period, provider)

    async def catch_up(self, provider: Any) -> None:
        periods = {(kind, period_for_date(kind, date.fromisoformat(str(item["date"])))) for item in self.storage.iter_daily_metadata() for kind in ("weekly", "monthly")}
        for kind, period in sorted(periods):
            if period_dates(kind, period)[-1] >= date.today():
                continue
            self.refresh_staleness(kind, period)
            if not self.storage.has_review(kind, period):
                await self.generate(kind, period, provider)

    def mark_daily_changed(self, diary_date: date, reason: str, core_changed: bool = True) -> None:
        if not core_changed:
            return
        value = diary_date.isoformat()
        for kind, period, metadata in self.storage.iter_review_metadata() or ():
            if value not in {day.isoformat() for day in period_dates(kind, period)}:
                continue
            metadata["summary_stale"] = True
            metadata["stale_reason"] = f"{reason}:{value}"
            metadata.setdefault("stale_since", self._now())
            atomic_write_json(self.storage.review_metadata_path(kind, period), metadata)

    def refresh_staleness(self, kind: str, period: str) -> None:
        metadata = self.storage.load_review_metadata(kind, period)
        if not metadata:
            return
        fingerprints = metadata.get("source_fingerprints", {})
        for value in (item.isoformat() for item in period_dates(kind, period)):
            daily = self.storage.load_metadata(value)
            if daily is not None and fingerprints.get(value) != core_fingerprint(daily):
                self.mark_daily_changed(date.fromisoformat(value), "daily_metadata_changed")
                return

    async def _call_provider(self, provider: Any, kind: str, period: str, daily: list[dict[str, Any]]) -> str:
        material = [{key: item.get(key, []) for key in ("date", "title", "mood", "mood_score", "topics", "people", "projects", "events", "highlights", "unresolved", "ongoing_topics")} for item in daily]
        prompt = json.dumps({"kind": kind, "period": period, "daily": material}, ensure_ascii=False)
        system = "Return JSON only: markdown,title,topics,people,projects,events,highlights,unresolved. Events require summary,source_dates,facts,inferences. Use only supplied daily facts and dates."
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

    def _parse_response(self, raw: str, kind: str, period: str, daily: list[dict[str, Any]], covered_dates: list[str], missing_dates: list[str], provider: Any) -> tuple[str, dict[str, Any]]:
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
        return str(data["markdown"]).strip() + "\n", metadata

    def _save_state(self, kind: str, period: str, stage: str, **extra: str) -> None:
        atomic_write_json(self.storage.review_state_path, {"kind": kind, "pending_period": period, "stage": stage, "updated_at": self._now(), **extra})

    @staticmethod
    def _strings(value: Any) -> list[str]:
        return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip())) if isinstance(value, list) else []

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

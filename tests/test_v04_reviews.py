import asyncio
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from diary.storage import DiaryStorage, atomic_write_json


def daily(storage, value, *, project="Godot", topic="AstrBot", event="progress"):
    atomic_write_json(storage.metadata_path(value), {
        "date": value, "title": f"Diary {value}", "topics": [topic], "people": ["Alice"],
        "projects": [project], "highlights": [event], "unresolved": ["follow up"], "ongoing_topics": [topic],
        "mood": "calm", "mood_score": 0.5, "events": [{"summary": event, "facts": [event], "memory_ids": [value]}],
    })


class Provider:
    def __init__(self, response=None):
        self.calls = 0
        self.response = response or {
            "markdown": "# review\n\nA sourced review.", "title": "review", "events": [
                {"summary": "progress", "source_dates": ["2026-07-20"], "facts": ["progress"]}
            ], "topics": ["AstrBot"], "projects": ["Godot"], "highlights": ["progress"], "unresolved": ["follow up"],
        }

    async def text_chat(self, **_kwargs):
        self.calls += 1
        return type("Response", (), {"completion_text": json.dumps(self.response)})()


class ReviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_week_boundary_allows_missing_days_and_is_idempotent(self):
        from diary.reviews import ReviewService
        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp))
            daily(storage, "2026-07-20")
            daily(storage, "2026-07-26")
            service = ReviewService(storage)
            provider = Provider()
            self.assertTrue(await service.generate("weekly", "2026-W30", provider))
            metadata = storage.load_review_metadata("weekly", "2026-W30")
            self.assertEqual(metadata["covered_dates"], ["2026-07-20", "2026-07-26"])
            self.assertEqual(len(metadata["missing_dates"]), 5)
            self.assertTrue(await service.generate("weekly", "2026-W30", provider))
            self.assertEqual(provider.calls, 1)

    async def test_monthly_rewrite_creates_backup_and_daily_change_marks_stale(self):
        from diary.reviews import ReviewService
        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp))
            daily(storage, "2026-07-31")
            service = ReviewService(storage)
            self.assertTrue(await service.generate("monthly", "2026-07", Provider()))
            self.assertTrue(await service.generate("monthly", "2026-07", Provider(), force=True))
            self.assertEqual(len(list(storage.review_backup_root.glob("monthly/2026-07/*/*.md"))), 1)
            service.mark_daily_changed(date(2026, 7, 31), "daily_rewritten")
            metadata = storage.load_review_metadata("monthly", "2026-07")
            self.assertTrue(metadata["summary_stale"])
            self.assertIn("daily_rewritten", metadata["stale_reason"])

    async def test_review_parser_rejects_provider_fabricated_source_date(self):
        from diary.reviews import ReviewService
        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp)); daily(storage, "2026-07-20")
            response = {"markdown": "# review", "title": "review", "events": [{"summary": "fake", "source_dates": ["1999-01-01"], "facts": ["fake"]}]}
            self.assertTrue(await ReviewService(storage).generate("weekly", "2026-W30", Provider(response)))
            self.assertEqual(storage.load_review_metadata("weekly", "2026-W30")["events"], [])

    async def test_ended_period_daily_write_triggers_summary(self):
        from diary.reviews import ReviewService
        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp)); daily(storage, "2025-07-27")
            await ReviewService(storage).after_daily_written(date(2025, 7, 27), Provider(), "daily_added")
            self.assertTrue(storage.has_review("weekly", "2025-W30"))


class RetrievalTests(unittest.TestCase):
    def test_retrieval_returns_dated_sources_and_no_result(self):
        from diary.retrieval import search_daily
        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp)); daily(storage, "2025-07-25", event="AstrBot release")
            self.assertEqual(search_daily(storage, "missing"), [])
            self.assertEqual([item.date for item in search_daily(storage, "release")], ["2025-07-25"])

    def test_on_this_day_is_cross_year_and_timeline_is_date_sorted(self):
        from diary.retrieval import on_this_day, timeline
        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp))
            daily(storage, "2024-07-25", event="first")
            daily(storage, "2025-07-25", event="latest")
            daily(storage, "2026-07-25", event="today")
            self.assertEqual([item.date for item in on_this_day(storage, date(2026, 7, 25))], ["2025-07-25", "2024-07-25"])
            result = timeline(storage, "Godot", "projects")
            self.assertEqual((result.first.date, result.latest.date), ("2024-07-25", "2026-07-25"))


class AskDiaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_ask_diary_returns_no_result_without_calling_provider(self):
        from diary.ask_diary import ask_diary
        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp)); provider = Provider()
            answer = await ask_diary(storage, "nothing", provider)
            self.assertIn("未检索", answer)
            self.assertEqual(provider.calls, 0)

    async def test_ask_diary_filters_fabricated_sources(self):
        from diary.ask_diary import ask_diary
        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp)); daily(storage, "2025-07-25", event="AstrBot release")
            provider = Provider({"answer": "invented", "source_dates": ["1999-01-01"]})
            answer = await ask_diary(storage, "release", provider)
            self.assertNotIn("1999-01-01", answer)
            self.assertIn("2025-07-25", answer)

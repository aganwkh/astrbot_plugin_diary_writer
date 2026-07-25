import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from diary.storage import DiaryStorage, atomic_write_json
from tests.test_v04_reviews import Provider, daily


class PeriodAndStaleAuditTests(unittest.IsolatedAsyncioTestCase):
    async def test_cross_year_iso_week_and_leap_month_coverage(self):
        from diary.reviews import ReviewService, period_dates
        self.assertEqual([item.isoformat() for item in period_dates("weekly", "2020-W53")], ["2020-12-28", "2020-12-29", "2020-12-30", "2020-12-31", "2021-01-01", "2021-01-02", "2021-01-03"])
        self.assertEqual(len(period_dates("monthly", "2024-02")), 29)
        self.assertEqual(len(period_dates("monthly", "2025-02")), 28)
        self.assertEqual(len(period_dates("monthly", "2025-04")), 30)
        self.assertEqual(len(period_dates("monthly", "2025-01")), 31)
        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp)); daily(storage, "2020-12-31")
            await ReviewService(storage).generate("weekly", "2020-W53", Provider())
            metadata = storage.load_review_metadata("weekly", "2020-W53")
            self.assertEqual(metadata["covered_dates"], ["2020-12-31"])
            self.assertIn("2021-01-01", metadata["missing_dates"])

    async def test_core_change_marks_week_and_month_but_technical_change_does_not(self):
        from diary.reviews import ReviewService
        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp)); daily(storage, "2025-12-31")
            service = ReviewService(storage)
            await service.generate("weekly", "2026-W01", Provider())
            await service.generate("monthly", "2025-12", Provider())
            metadata = storage.load_metadata("2025-12-31"); metadata["model"] = "new-model"; atomic_write_json(storage.metadata_path("2025-12-31"), metadata)
            service.refresh_staleness("weekly", "2026-W01")
            service.refresh_staleness("monthly", "2025-12")
            self.assertFalse(storage.load_review_metadata("weekly", "2026-W01")["summary_stale"])
            self.assertFalse(storage.load_review_metadata("monthly", "2025-12")["summary_stale"])
            metadata["highlights"] = ["changed fact"]; atomic_write_json(storage.metadata_path("2025-12-31"), metadata)
            service.refresh_staleness("weekly", "2026-W01")
            service.refresh_staleness("monthly", "2025-12")
            self.assertTrue(storage.load_review_metadata("weekly", "2026-W01")["summary_stale"])
            self.assertTrue(storage.load_review_metadata("monthly", "2025-12")["summary_stale"])

    async def test_review_replace_failure_preserves_formal_pair_and_daily_data(self):
        import diary.storage as module
        from diary.reviews import ReviewService
        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp)); daily(storage, "2025-07-27", event="daily fact")
            service = ReviewService(storage); await service.generate("weekly", "2025-W30", Provider())
            before_daily = storage.metadata_path("2025-07-27").read_text(encoding="utf-8")
            before_review = storage.review_path("weekly", "2025-W30").read_text(encoding="utf-8")
            original = module.os.replace
            def fail_metadata(source, target):
                if Path(target) == storage.review_metadata_path("weekly", "2025-W30"):
                    raise OSError("disk full")
                return original(source, target)
            module.os.replace = fail_metadata
            try:
                self.assertIsNone(await service.generate("weekly", "2025-W30", Provider(), force=True))
            finally:
                module.os.replace = original
            self.assertEqual(storage.review_path("weekly", "2025-W30").read_text(encoding="utf-8"), before_review)
            self.assertEqual(storage.metadata_path("2025-07-27").read_text(encoding="utf-8"), before_daily)
            self.assertEqual(json.loads(storage.review_state_path.read_text(encoding="utf-8"))["stage"], "failed")


class RetrievalAuditTests(unittest.IsolatedAsyncioTestCase):
    async def test_ask_diary_never_uses_unverified_llm_answer(self):
        from diary.ask_diary import ask_diary
        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp)); daily(storage, "2025-07-25", event="真实事件")
            provider = Provider({"answer": "虚构人物完成了虚构项目", "source_dates": ["2025-07-25"]})
            answer = await ask_diary(storage, "真实事件", provider)
            self.assertNotIn("虚构人物", answer)
            self.assertIn("2025-07-25", answer)
            self.assertEqual(provider.calls, 0)

    async def test_v03_fixture_is_searchable_and_reviewable_without_migration(self):
        from diary.ask_diary import ask_diary
        from diary.retrieval import on_this_day, timeline
        from diary.reviews import ReviewService
        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp))
            atomic_write_json(storage.metadata_path("2025-07-27"), {"date": "2025-07-27", "title": "v0.3", "topics": ["AstrBot"], "people": ["Alice"], "projects": ["Godot"], "highlights": ["legacy highlight"], "events": [{"summary": "legacy event", "facts": ["legacy event"]}]})
            self.assertIn("2025-07-27", await ask_diary(storage, "legacy", None))
            self.assertTrue(await ReviewService(storage).generate("weekly", "2025-W30", Provider()))
            self.assertEqual(on_this_day(storage, date(2026, 7, 27))[0].date, "2025-07-27")
            self.assertEqual(timeline(storage, "godot", "projects").latest.date, "2025-07-27")

    async def test_metadata_search_handles_empty_duplicate_case_and_chinese_partial_terms(self):
        from diary.retrieval import search_daily, timeline
        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp))
            atomic_write_json(storage.metadata_path("2025-07-20"), {"date": "2025-07-20", "title": "", "topics": ["AstrBot", "AstrBot"], "people": ["张三"], "projects": ["Godot"], "highlights": [], "events": []})
            atomic_write_json(storage.metadata_path("2025-07-21"), {"date": "2025-07-21", "title": "", "topics": [], "people": ["张三"], "projects": ["Godot"], "highlights": ["中文进展"], "events": [{"summary": "构建完成", "facts": []}]})
            self.assertEqual([item.date for item in search_daily(storage, "god")], ["2025-07-20", "2025-07-21"])
            self.assertEqual([item.date for item in search_daily(storage, "张")], ["2025-07-20", "2025-07-21"])
            self.assertEqual([item.date for item in search_daily(storage, "中文")], ["2025-07-21"])
            self.assertEqual([item.date for item in timeline(storage, "GODOT", "projects").entries], ["2025-07-20", "2025-07-21"])

import json
import math
import tempfile
import unittest
from pathlib import Path

from diary.storage import DiaryStorage, atomic_write_json


def daily(storage, value, *, event="daily event"):
    atomic_write_json(storage.metadata_path(value), {
        "date": value, "title": f"Diary {value}", "mood": "calm", "mood_score": 0.5,
        "topics": ["AstrBot"], "people": ["Alice"], "projects": ["Godot"],
        "events": [{"summary": event, "facts": [event], "memory_ids": [value]}],
        "highlights": [event], "unresolved": [], "ongoing_topics": [],
    })


class Provider:
    def __init__(self):
        self.calls = 0
        self.prompt = {}

    async def text_chat(self, **kwargs):
        self.calls += 1
        self.prompt = json.loads(kwargs["prompt"])
        return type("Response", (), {"completion_text": json.dumps({
            "markdown": "# 2024 annual review", "title": "2024 annual review",
            "events": [{"summary": "daily event", "source_dates": ["2024-01-01"], "facts": ["daily event"]}],
            "topics": ["AstrBot"], "projects": ["Godot"], "highlights": ["daily event"], "unresolved": [],
        })})()


class YearlyReviewTests(unittest.IsolatedAsyncioTestCase):
    def test_calendar_year_day_lengths_cover_leap_and_common_years(self):
        from diary.reviews import period_dates

        self.assertEqual((period_dates("yearly", "2024")[0].isoformat(), len(period_dates("yearly", "2024"))), ("2024-01-01", 366))
        self.assertEqual((period_dates("yearly", "2025")[-1].isoformat(), len(period_dates("yearly", "2025"))), ("2025-12-31", 365))

    def test_yearly_facts_ignore_malformed_or_summaryless_events(self):
        from diary.reviews import ReviewService

        with tempfile.TemporaryDirectory() as temp:
            facts = ReviewService(DiaryStorage(Path(temp)))._daily_facts([
                {"date": "2024-01-01", "events": None},
                {"date": "2024-01-02", "events": "not a list"},
                {"date": "2024-01-03", "events": [{"summary": ""}, {"facts": ["missing summary"]}, {"summary": "valid"}, "wrong"]},
            ])

            self.assertEqual(facts["aggregates"]["event_count"], 1)
            self.assertEqual(facts["events"], [{"summary": "valid", "source_dates": ["2024-01-03"], "facts": [], "inferences": [], "memory_ids": []}])

    async def test_yearly_generation_records_missing_months_and_roundtrips(self):
        from diary.reviews import ReviewService

        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp))
            daily(storage, "2024-01-01")
            daily(storage, "2024-03-01")
            provider = Provider()

            self.assertTrue(await ReviewService(storage).generate("yearly", "2024", provider))
            metadata = storage.load_review_metadata("yearly", "2024")
            self.assertEqual(metadata["covered_periods"], ["2024-01", "2024-03"])
            self.assertEqual(len(metadata["missing_periods"]), 10)
            self.assertEqual(len(metadata["missing_dates"]), 364)
            self.assertEqual(metadata["source_diary_dates"], ["2024-01-01", "2024-03-01"])
            self.assertTrue(storage.has_review("yearly", "2024"))
            self.assertTrue(await ReviewService(storage).generate("yearly", "2024", provider))
            self.assertEqual(provider.calls, 1)

    async def test_yearly_ignores_non_finite_mood_scores_in_prompt_and_aggregates(self):
        from diary.reviews import ReviewService

        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp))
            daily(storage, "2024-01-01")
            first = storage.load_metadata("2024-01-01")
            first["mood_score"] = math.nan
            atomic_write_json(storage.metadata_path("2024-01-01"), first)
            daily(storage, "2024-01-02")
            second = storage.load_metadata("2024-01-02")
            second["mood_score"] = math.inf
            atomic_write_json(storage.metadata_path("2024-01-02"), second)
            daily(storage, "2024-01-03")
            provider = Provider()

            self.assertTrue(await ReviewService(storage).generate("yearly", "2024", provider))
            metadata = storage.load_review_metadata("yearly", "2024")
            self.assertEqual(metadata["fact_aggregates"]["mood_score"], {"count": 1, "average": 0.5})
            self.assertEqual([item["mood_score"] for item in provider.prompt["daily"]], [None, None, 0.5])

    async def test_yearly_prompt_keeps_daily_facts_separate_from_monthly_context(self):
        from diary.reviews import ReviewService

        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp))
            daily(storage, "2024-01-01", event="one daily event")
            monthly_metadata = {
                "kind": "monthly", "period": "2024-01", "title": "January",
                "events": [{"summary": "one daily event", "source_dates": ["2024-01-01"]}],
                "highlights": ["one daily event"], "topics": ["AstrBot", "MonthlyOnly"], "projects": ["Godot", "MonthlyOnly"], "unresolved": [],
            }
            atomic_write_json(storage.review_metadata_path("monthly", "2024-01"), monthly_metadata)
            storage.review_path("monthly", "2024-01").parent.mkdir(parents=True, exist_ok=True)
            storage.review_path("monthly", "2024-01").write_text("# January\n", encoding="utf-8")
            provider = Provider()

            self.assertTrue(await ReviewService(storage).generate("yearly", "2024", provider))
            self.assertEqual([item["date"] for item in provider.prompt["daily"]], ["2024-01-01"])
            self.assertEqual(len(provider.prompt["daily"][0]["events"]), 1)
            self.assertEqual([item["period"] for item in provider.prompt["monthly_context"]], ["2024-01"])
            metadata = storage.load_review_metadata("yearly", "2024")
            self.assertEqual(metadata["source_monthly_periods"], ["2024-01"])
            self.assertIn("2024-01", metadata["source_monthly_fingerprints"])
            self.assertEqual(metadata["fact_aggregates"]["event_count"], 1)
            self.assertEqual(metadata["fact_aggregates"]["topic_counts"], {"AstrBot": 1})
            self.assertEqual(metadata["fact_aggregates"]["project_counts"], {"Godot": 1})
            self.assertEqual(metadata["fact_aggregates"]["mood_score"], {"count": 1, "average": 0.5})
            self.assertEqual(metadata["topics"], ["AstrBot"])
            self.assertEqual(metadata["projects"], ["Godot"])
            self.assertEqual(metadata["events"], [{"summary": "one daily event", "source_dates": ["2024-01-01"], "facts": ["one daily event"], "inferences": [], "memory_ids": ["2024-01-01"]}])

    async def test_yearly_excludes_stale_monthly_context(self):
        from diary.reviews import ReviewService

        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp))
            daily(storage, "2024-01-01")
            storage.write_review("monthly", "2024-01", "# stale January\n", {
                "kind": "monthly", "period": "2024-01", "title": "Stale January", "highlights": [], "topics": [], "projects": [], "unresolved": [], "summary_stale": True,
            })
            provider = Provider()

            self.assertTrue(await ReviewService(storage).generate("yearly", "2024", provider))
            self.assertEqual(provider.prompt["monthly_context"], [])
            self.assertEqual(storage.load_review_metadata("yearly", "2024")["source_monthly_periods"], [])

    async def test_yearly_refresh_marks_stale_when_cited_monthly_changes(self):
        from diary.reviews import ReviewService

        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp))
            daily(storage, "2024-01-01")
            storage.write_review("monthly", "2024-01", "# January\n", {
                "kind": "monthly", "period": "2024-01", "title": "January", "highlights": [], "topics": [], "projects": [], "unresolved": [],
            })
            service = ReviewService(storage)
            self.assertTrue(await service.generate("yearly", "2024", Provider()))
            storage.write_review("monthly", "2024-01", "# Updated January\n", {
                "kind": "monthly", "period": "2024-01", "title": "Updated January", "highlights": [], "topics": [], "projects": [], "unresolved": [],
            }, backup_existing=True)

            service.refresh_staleness("yearly", "2024")
            metadata = storage.load_review_metadata("yearly", "2024")
            self.assertTrue(metadata["summary_stale"])
            self.assertIn("monthly_review_changed:2024-01", metadata["stale_reason"])
            self.assertTrue(metadata["stale_since"])

    async def test_forced_monthly_rewrite_immediately_marks_cited_yearly_stale(self):
        from diary.reviews import ReviewService

        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp))
            daily(storage, "2024-01-01")
            storage.write_review("monthly", "2024-01", "# January\n", {
                "kind": "monthly", "period": "2024-01", "title": "January", "highlights": [], "topics": [], "projects": [], "unresolved": [],
            })
            service = ReviewService(storage)
            self.assertTrue(await service.generate("yearly", "2024", Provider()))

            self.assertTrue(await service.generate("monthly", "2024-01", Provider(), force=True))
            metadata = storage.load_review_metadata("yearly", "2024")
            self.assertTrue(metadata["summary_stale"])
            self.assertIn("monthly_review_changed:2024-01", metadata["stale_reason"])

    async def test_forced_monthly_regeneration_with_same_core_keeps_cited_yearly_fresh(self):
        from diary.reviews import ReviewService

        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp))
            daily(storage, "2024-01-01")
            service = ReviewService(storage)
            self.assertTrue(await service.generate("monthly", "2024-01", Provider()))
            self.assertTrue(await service.generate("yearly", "2024", Provider()))

            self.assertTrue(await service.generate("monthly", "2024-01", Provider(), force=True))
            self.assertFalse(storage.load_review_metadata("yearly", "2024")["summary_stale"])

    async def test_yearly_refresh_ignores_cited_monthly_technical_changes(self):
        from diary.reviews import ReviewService

        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp))
            daily(storage, "2024-01-01")
            monthly = {"kind": "monthly", "period": "2024-01", "title": "January", "highlights": [], "topics": [], "projects": [], "unresolved": [], "generated_at": "first"}
            storage.write_review("monthly", "2024-01", "# January\n", monthly)
            service = ReviewService(storage)
            self.assertTrue(await service.generate("yearly", "2024", Provider()))
            monthly["generated_at"] = "second"
            storage.write_review("monthly", "2024-01", "# January\n", monthly)

            service.refresh_staleness("yearly", "2024")
            self.assertFalse(storage.load_review_metadata("yearly", "2024")["summary_stale"])

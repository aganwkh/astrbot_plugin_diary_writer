import asyncio
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from diary.storage import DiaryStorage
from tests.test_v05_yearly import Provider, daily


class FailingProvider:
    async def text_chat(self, **_kwargs):
        raise RuntimeError("provider unavailable")


class BlockingProvider(Provider):
    def __init__(self):
        super().__init__()
        self.started, self.release = asyncio.Event(), asyncio.Event()

    async def text_chat(self, **kwargs):
        self.started.set()
        await self.release.wait()
        return await super().text_chat(**kwargs)


class ChangedProvider(Provider):
    async def text_chat(self, **kwargs):
        self.calls += 1
        self.prompt = json.loads(kwargs["prompt"])
        return type("Response", (), {"completion_text": json.dumps({
            "markdown": "# changed review", "title": "changed review",
            "events": [{"summary": "changed", "source_dates": ["2024-01-01"], "facts": ["changed"]}],
            "topics": ["Changed"], "projects": ["Changed"], "highlights": ["changed"], "unresolved": [],
        })})()


class ReviewReliabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_daily_change_during_provider_call_leaves_new_review_stale(self):
        from diary.reviews import ReviewService

        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp)); daily(storage, "2024-01-01", event="before")
            service, provider = ReviewService(storage), BlockingProvider()
            task = asyncio.create_task(service.generate("weekly", "2024-W01", provider))
            await asyncio.wait_for(provider.started.wait(), 1)
            daily(storage, "2024-01-01", event="after")
            provider.release.set()
            self.assertTrue(await task)
            metadata = storage.load_review_metadata("weekly", "2024-W01")
            self.assertTrue(metadata["summary_stale"])
            self.assertIn("daily_metadata_changed:2024-01-01", metadata["stale_reason"])

    async def test_monthly_change_during_yearly_provider_call_leaves_yearly_stale(self):
        from diary.reviews import ReviewService

        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp)); daily(storage, "2024-01-01")
            service = ReviewService(storage)
            self.assertTrue(await service.generate("monthly", "2024-01", Provider()))
            provider = BlockingProvider()
            task = asyncio.create_task(service.generate("yearly", "2024", provider))
            await asyncio.wait_for(provider.started.wait(), 1)
            self.assertTrue(await service.generate("monthly", "2024-01", ChangedProvider(), force=True))
            provider.release.set()
            self.assertTrue(await task)
            metadata = storage.load_review_metadata("yearly", "2024")
            self.assertTrue(metadata["summary_stale"])
            self.assertIn("monthly_review_changed:2024-01", metadata["stale_reason"])

    async def test_review_state_keeps_unrelated_failure_after_later_success(self):
        from diary.reviews import ReviewService

        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp))
            daily(storage, "2024-01-01")
            service = ReviewService(storage)
            self.assertIsNone(await service.generate("weekly", "2024-W01", FailingProvider()))
            self.assertTrue(await service.generate("monthly", "2024-01", Provider()))

            state = json.loads(storage.review_state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["entries"]["weekly:2024-W01"]["stage"], "failed")
            self.assertEqual(state["entries"]["monthly:2024-01"]["stage"], "succeeded")

    async def test_refresh_marks_yearly_stale_when_fingerprinted_daily_is_deleted(self):
        from diary.reviews import ReviewService

        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp))
            daily(storage, "2024-01-01")
            service = ReviewService(storage)
            self.assertTrue(await service.generate("yearly", "2024", Provider()))
            storage.metadata_path("2024-01-01").unlink()

            service.refresh_staleness("yearly", "2024")
            metadata = storage.load_review_metadata("yearly", "2024")
            self.assertTrue(metadata["summary_stale"])
            self.assertIn("daily_metadata_deleted:2024-01-01", metadata["stale_reason"])

    async def test_yearly_provider_material_is_bounded_without_raw_event_facts(self):
        from diary.reviews import ReviewService

        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp))
            records = [{
                "date": (date(2024, 1, 1).fromordinal(date(2024, 1, 1).toordinal() + day - 1)).isoformat(), "title": f"day {day}",
                "mood": "calm", "mood_score": 0.5, "topics": ["AstrBot"], "people": [], "projects": ["Godot"],
                "events": [{"summary": f"event {day}", "facts": ["long raw fact"], "memory_ids": [str(day)]}], "highlights": [], "unresolved": [],
            } for day in range(1, 122)]
            provider = Provider()

            await ReviewService(storage)._call_provider(provider, "yearly", "2024", records)
            self.assertEqual(len(provider.prompt["daily"]), 120)
            self.assertEqual(len(provider.prompt["daily_source_dates"]), 121)
            self.assertNotIn("facts", provider.prompt["daily"][0]["events"][0])
            self.assertEqual(provider.prompt["daily_aggregates"]["event_count"], 121)

    async def test_december_daily_triggers_yearly_and_daily_change_marks_it_stale(self):
        from diary.reviews import ReviewService

        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp))
            daily(storage, "2024-12-31")
            service = ReviewService(storage)
            await service.after_daily_written(date(2024, 12, 31), Provider(), "daily_added")
            self.assertTrue(storage.has_review("yearly", "2024"))
            service.mark_daily_changed(date(2024, 12, 31), "daily_rewritten")
            self.assertTrue(storage.load_review_metadata("yearly", "2024")["summary_stale"])

    async def test_catch_up_generates_ended_yearly_period(self):
        from diary.reviews import ReviewService

        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp))
            daily(storage, "2024-12-31")
            await ReviewService(storage).catch_up(Provider())
            self.assertTrue(storage.has_review("yearly", "2024"))

    async def test_yearly_force_backup_and_write_failure_preserve_existing_review(self):
        import diary.storage as storage_module
        from diary.reviews import ReviewService

        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp))
            daily(storage, "2024-01-01")
            service = ReviewService(storage)
            self.assertTrue(await service.generate("yearly", "2024", Provider()))
            self.assertTrue(await service.generate("yearly", "2024", Provider(), force=True))
            self.assertTrue(await service.generate("yearly", "2024", Provider(), force=True))
            self.assertEqual(len(list(storage.review_backup_root.glob("yearly/2024/*/*.md"))), 2)
            before = storage.review_path("yearly", "2024").read_text(encoding="utf-8")
            original = storage_module.os.replace

            def fail_metadata(source, target):
                if Path(target) == storage.review_metadata_path("yearly", "2024"):
                    raise OSError("disk full")
                return original(source, target)

            storage_module.os.replace = fail_metadata
            try:
                self.assertIsNone(await service.generate("yearly", "2024", Provider(), force=True))
            finally:
                storage_module.os.replace = original
            self.assertEqual(storage.review_path("yearly", "2024").read_text(encoding="utf-8"), before)

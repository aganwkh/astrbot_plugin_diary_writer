import asyncio
import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from diary.config import DiaryConfig
from diary.corrections import CorrectionService
from diary.maintenance import GLOBAL_MAINTENANCE_GATE, MaintenanceGate
from diary.models import SourceMemory
from diary.reviews import ReviewService
from diary.service import DiaryService
from diary.storage import DiaryStorage, atomic_write_json, atomic_write_text


class Source:
    def read_day(self, _day, limit=80):
        return [SourceMemory("memory-1", datetime(2025, 7, 26, 9), "Recorded event")]


class DailyProvider:
    calls = 0

    async def text_chat(self, **_kwargs):
        self.calls += 1
        return type("Response", (), {"completion_text": json.dumps({
            "markdown": "# Daily\n\nRecorded event.", "title": "Daily",
            "events": [{"summary": "Recorded event", "memory_ids": ["memory-1"], "facts": ["Recorded event"]}],
        })})()


class ReviewProvider:
    calls = 0

    async def text_chat(self, **_kwargs):
        self.calls += 1
        return type("Response", (), {"completion_text": json.dumps({
            "markdown": "# Monthly", "title": "Monthly", "events": [],
        })})()


class MaintenanceGateIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_restore_holds_daily_review_and_correction_writers(self):
        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp)); gate = MaintenanceGate()
            atomic_write_text(storage.diary_path("2025-07-25"), "# Existing\n")
            atomic_write_json(storage.metadata_path("2025-07-25"), {
                "date": "2025-07-25", "title": "Existing", "projects": ["Alpha"], "events": [],
            })
            daily_provider, review_provider = DailyProvider(), ReviewProvider()
            reviews = ReviewService(storage, gate=gate)
            daily = DiaryService(DiaryConfig(), storage, Source(), gate=gate)
            corrections = CorrectionService(storage, reviews, gate=gate)

            async with gate.restore():
                daily_task = asyncio.create_task(daily.generate(date(2025, 7, 26), daily_provider))
                review_task = asyncio.create_task(reviews.generate("monthly", "2025-07", review_provider))
                correction_task = asyncio.create_task(corrections.replace("2025-07-25", "projects", "Alpha", "Beta"))
                await asyncio.sleep(0)
                self.assertTrue(gate.restoring)
                self.assertFalse(storage.has_any_diary("2025-07-26"))
                self.assertFalse(storage.has_review("monthly", "2025-07"))
                self.assertEqual(storage.load_metadata("2025-07-25")["projects"], ["Alpha"])
                self.assertEqual((daily_provider.calls, review_provider.calls), (0, 0))

            daily_result, review_result, correction = await asyncio.gather(daily_task, review_task, correction_task)
            self.assertTrue(daily_result)
            self.assertTrue(review_result)
            self.assertEqual(correction["status"], "active")
            self.assertTrue(storage.has_any_diary("2025-07-26"))
            self.assertTrue(storage.has_review("monthly", "2025-07"))
            self.assertEqual(storage.load_metadata("2025-07-25")["projects"], ["Beta"])


class GlobalMaintenanceGateTests(unittest.TestCase):
    def test_global_gate_can_open_in_successive_event_loops(self):
        async def use_gate():
            async with GLOBAL_MAINTENANCE_GATE.operation():
                self.assertFalse(GLOBAL_MAINTENANCE_GATE.restoring)

        asyncio.run(use_gate())
        asyncio.run(use_gate())


if __name__ == "__main__":
    unittest.main()

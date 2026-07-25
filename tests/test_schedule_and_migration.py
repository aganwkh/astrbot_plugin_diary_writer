import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from diary.storage import DiaryStorage


class ScheduleAndMigrationTests(unittest.TestCase):
    def test_legacy_markdown_is_a_diary_and_gets_sidecar_without_overwrite(self):
        from diary.migration import migrate_legacy_markdown
        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp)); path = storage.diary_path("2026-07-25")
            path.parent.mkdir(parents=True); path.write_text("# old", encoding="utf-8")
            self.assertTrue(storage.has_any_diary("2026-07-25"))
            migrate_legacy_markdown(storage, "2026-07-25")
            self.assertEqual(path.read_text(encoding="utf-8"), "# old")
            self.assertTrue(storage.metadata_path("2026-07-25").exists())

    def test_schedule_checks_inactivity_and_four_am_fallback(self):
        from diary.schedule import should_generate, should_run_regular_check
        now = datetime(2026, 7, 26, 1, 0)
        self.assertFalse(should_generate(now, now - timedelta(minutes=89), 90))
        self.assertTrue(should_generate(now, now - timedelta(minutes=90), 90))
        self.assertTrue(should_generate(datetime(2026, 7, 26, 4), now - timedelta(minutes=60), 60))
        self.assertFalse(should_run_regular_check(datetime(2026, 7, 26, 0, 20), 30))
        self.assertTrue(should_run_regular_check(datetime(2026, 7, 26, 0, 30), 30))

import importlib
import tempfile
import unittest
from pathlib import Path

from diary.models import DiaryMetadata, GenerationState


def load_storage():
    try:
        return importlib.import_module("diary.storage")
    except ModuleNotFoundError as exc:
        raise AssertionError("missing storage module") from exc


class StorageTests(unittest.TestCase):
    def test_rewrite_keeps_timestamped_markdown_and_metadata_backup(self):
        storage_module = load_storage()
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = storage_module.DiaryStorage(Path(temp_dir))
            storage.write_diary("2026-07-25", "old", DiaryMetadata(date="2026-07-25", title="old"))
            storage.write_diary("2026-07-25", "new", DiaryMetadata(date="2026-07-25", title="new"), backup_existing=True)
            self.assertEqual(storage.diary_path("2026-07-25").read_text(encoding="utf-8"), "new")
            self.assertEqual(len(list(storage.backup_root.glob("2026-07-25/*/*.md"))), 1)
            self.assertEqual(len(list(storage.backup_root.glob("2026-07-25/*/*.json"))), 1)

    def test_atomic_write_leaves_only_final_file(self):
        storage_module = load_storage()
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "result.txt"
            storage_module.atomic_write_text(target, "complete")
            self.assertEqual(target.read_text(encoding="utf-8"), "complete")
            self.assertEqual(list(Path(temp_dir).glob("*.tmp")), [])

    def test_generation_state_round_trips_without_diary_files(self):
        storage_module = load_storage()
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = storage_module.DiaryStorage(Path(temp_dir))
            storage.save_generation_state(GenerationState(pending_date="2026-07-25", stage="failed", retry_count=2, last_error="offline"))
            state = storage.load_generation_state()
            self.assertEqual((state.pending_date, state.stage, state.retry_count, state.last_error), ("2026-07-25", "failed", 2, "offline"))

    def test_activity_state_survives_restart_storage_reopen(self):
        storage_module = load_storage()
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = storage_module.DiaryStorage(Path(temp_dir))
            storage.save_activity("2026-07-25T12:00:00+00:00")
            self.assertEqual(storage_module.DiaryStorage(Path(temp_dir)).load_activity(), "2026-07-25T12:00:00+00:00")


if __name__ == "__main__":
    unittest.main()

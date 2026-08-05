import asyncio
import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from diary.config import DiaryConfig
from diary.models import ContinuityState, DiaryMetadata, SourceMemory
from diary.storage import DiaryStorage


class Source:
    def read_day(self, _day, limit=80):
        return [SourceMemory("real-id", datetime(2026, 7, 25, 9), "A real source fact.")]


class Provider:
    def __init__(self):
        self.calls = 0

    async def text_chat(self, **_kwargs):
        self.calls += 1
        await asyncio.sleep(0)
        return type("Response", (), {"completion_text": json.dumps({
            "markdown": "# diary\n\nA real source fact.",
            "title": "diary",
            "events": [{"summary": "fact", "memory_ids": ["real-id"], "facts": ["A real source fact."]}],
        })})()


class V03RegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_v02_markdown_gets_metadata_without_regeneration(self):
        from diary.service import DiaryService
        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp))
            storage.diary_path("2026-07-25").parent.mkdir(parents=True)
            storage.diary_path("2026-07-25").write_text("# old", encoding="utf-8")
            provider = Provider()
            result = await DiaryService(DiaryConfig.from_mapping({"owner_ids": ["1"]}), storage, Source()).generate(date(2026, 7, 25), provider)
            self.assertTrue(result)
            self.assertEqual(provider.calls, 0)
            self.assertTrue(storage.metadata_path("2026-07-25").exists())

    async def test_preview_has_no_persistent_side_effects(self):
        from diary.service import DiaryService
        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp))
            draft = await DiaryService(DiaryConfig.from_mapping({"owner_ids": ["1"]}), storage, Source()).preview(date(2026, 7, 25), Provider())
            self.assertIn("# diary", draft)
            self.assertFalse(storage.diary_path("2026-07-25").exists())
            self.assertFalse(storage.metadata_path("2026-07-25").exists())
            self.assertFalse(storage.state_path.exists())
            self.assertFalse(storage.continuity_path.exists())

    async def test_same_date_concurrent_generation_calls_provider_once(self):
        from diary.service import DiaryService
        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp))
            provider = Provider()
            service = DiaryService(DiaryConfig.from_mapping({"owner_ids": ["1"], "provider_retry_count": 0}), storage, Source())
            results = await asyncio.gather(*(service.generate(date(2026, 7, 25), provider) for _ in range(2)))
            self.assertTrue(all(results))
            self.assertEqual(provider.calls, 1)

    async def test_failed_generation_leaves_continuity_unchanged(self):
        from diary.service import DiaryService

        class BrokenProvider:
            async def text_chat(self, **_kwargs):
                raise RuntimeError("offline")

        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp))
            storage.save_continuity(ContinuityState(previous_summary="before"))
            await DiaryService(DiaryConfig.from_mapping({"owner_ids": ["1"]}), storage, Source()).generate(date(2026, 7, 25), BrokenProvider())
            self.assertEqual(storage.load_continuity().previous_summary, "before")
            self.assertFalse(storage.diary_path("2026-07-25").exists())
            self.assertFalse(storage.metadata_path("2026-07-25").exists())
            self.assertEqual(storage.load_generation_state().stage, "failed")

class MigrationAndStorageTests(unittest.TestCase):
    def test_legacy_v02_directory_is_copied_without_changing_source_markdown(self):
        from diary.migration import migrate_legacy_directory
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            legacy = base / "diary_writer" / "diaries"
            legacy.mkdir(parents=True)
            source = legacy / "2026-07-25.md"
            source.write_text("# original v0.2\n\nkeep me", encoding="utf-8")
            storage = DiaryStorage(base / "astrbot_plugin_diary_writer")
            self.assertEqual(migrate_legacy_directory(legacy, storage), 1)
            self.assertEqual(source.read_text(encoding="utf-8"), "# original v0.2\n\nkeep me")
            self.assertTrue(storage.has_diary("2026-07-25"))

    def test_pair_write_rolls_back_when_second_replace_fails(self):
        import diary.storage as module
        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp))
            storage.write_diary("2026-07-25", "old", DiaryMetadata(date="2026-07-25", title="old"))
            original = module.os.replace

            def fail_metadata_replace(source, target):
                if Path(target) == storage.metadata_path("2026-07-25"):
                    raise OSError("disk full")
                return original(source, target)

            module.os.replace = fail_metadata_replace
            try:
                with self.assertRaises(OSError):
                    storage.write_diary("2026-07-25", "new", DiaryMetadata(date="2026-07-25", title="new"))
            finally:
                module.os.replace = original
            self.assertEqual(storage.diary_path("2026-07-25").read_text(encoding="utf-8"), "old")
            self.assertEqual(storage.load_metadata("2026-07-25")["title"], "old")

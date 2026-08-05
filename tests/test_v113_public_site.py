import tempfile
import unittest
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from diary.migration import migrate_plugin_data_directory
from diary.public_site import DiaryPublicSite
from diary.storage import DiaryStorage


class PluginDataMigrationTests(unittest.TestCase):
    def test_copies_existing_plugin_data_without_changing_the_source(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            legacy = base / "plugin_data" / "astrbot_plugin_diary_writer"
            (legacy / "diaries").mkdir(parents=True)
            source = legacy / "diaries" / "2026-08-01.md"
            source.write_text("# 旧日记\n", encoding="utf-8")
            storage = DiaryStorage(base / "installed-plugin" / "data" / "diary_writer")
            (storage.root.parent / "t2i_templates").mkdir(parents=True)

            self.assertTrue(migrate_plugin_data_directory(legacy, storage))
            self.assertEqual(storage.diary_path("2026-08-01").read_text(encoding="utf-8"), "# 旧日记\n")
            self.assertEqual(source.read_text(encoding="utf-8"), "# 旧日记\n")
            self.assertFalse(migrate_plugin_data_directory(legacy, storage))


class DiaryReaderAssetTests(unittest.TestCase):
    def test_date_strip_uses_versioned_assets_without_a_visual_scroll_cue(self):
        root = Path(__file__).resolve().parents[1] / "pages" / "diary-reader"
        index = (root / "index.html").read_text(encoding="utf-8")
        styles = (root / "styles.css").read_text(encoding="utf-8")

        self.assertIn('href="/styles.css?v=1.1.3-7"', index)
        self.assertIn('src="/app.js?v=1.1.3-7"', index)
        self.assertNotIn('scroll-cue', index)
        self.assertNotIn('scroll-cue', styles)
        self.assertNotIn('reader-kinds', index)
        self.assertNotIn('reader-kind', styles)

class PublicDiarySiteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.storage = DiaryStorage(Path(self.temp.name) / "data")
        self.storage.write_diary_data(
            "2026-08-01", "# 八月\n\n今天很平淡。",
            {"date": "2026-08-01", "title": "八月", "mood": "平淡", "tags": ["日记"], "events": [{"memory_ids": ["secret"]}]},
        )
        self.storage.write_diary_data(
            "2026-08-02", "# 第二天\n\n天气很好。",
            {"date": "2026-08-02", "title": "第二天", "mood": "开心", "tags": ["日常"]},
        )
        assets = Path(__file__).resolve().parents[1] / "pages" / "diary-reader"
        self.client = TestClient(TestServer(DiaryPublicSite(self.storage, assets).app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.temp.cleanup()

    async def test_public_endpoints_expose_only_reader_fields(self):
        self.assertEqual((await self.client.get("/")).status, 200)
        self.assertEqual((await self.client.get("/app.js")).status, 200)
        response = await self.client.get("/api/months")
        self.assertEqual((await response.json())["months"], ["2026-08"])

        response = await self.client.get("/api/entries?month=2026-08")
        entries = (await response.json())["entries"]
        self.assertEqual([entry["date"] for entry in entries], ["2026-08-02", "2026-08-01"])
        self.assertEqual(entries[0]["title"], "第二天")
        self.assertNotIn("events", entries[0])

        response = await self.client.get("/api/entries/2026-08-01")
        entry = (await response.json())["entry"]
        self.assertEqual(entry["markdown"], "# 八月\n\n今天很平淡。")
        self.assertNotIn("events", entry)
        self.assertNotIn("secret", str(entry))


    async def test_public_site_has_no_write_or_path_escape_route(self):
        self.assertEqual((await self.client.post("/api/months")).status, 405)
        self.assertEqual((await self.client.get("/api/entries/not-a-date")).status, 404)
        self.assertEqual((await self.client.get("/api/entries/%2E%2E%2Fmain.py")).status, 404)
        self.assertEqual((await self.client.get("/api/reviews/weekly")).status, 404)

    async def test_start_and_stop_own_the_listener_lifecycle(self):
        assets = Path(__file__).resolve().parents[1] / "pages" / "diary-reader"
        site = DiaryPublicSite(self.storage, assets, port=0)
        await site.start()
        self.assertIsNotNone(site._runner)
        await site.stop()
        self.assertIsNone(site._runner)

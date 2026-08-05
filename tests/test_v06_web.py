import tempfile
import unittest
from pathlib import Path

from diary.storage import DiaryStorage
import diary.web_api as web_api


class Request:
    def __init__(self, username="admin", query=None, payload=None):
        self.username = username
        self.query = query or {}
        self.payload = {} if payload is None else payload

    async def json(self, default=None):
        return self.payload


class Context:
    def __init__(self):
        self.routes = []

    def register_web_api(self, *route):
        self.routes.append(route)

    def get_provider_by_id(self, _provider_id):
        return None


class Config:
    generation_provider_id = ""
    can_auto_write = True
    auto_write_enabled = True
    inactive_minutes = 90
    fallback_inactive_minutes = 60
    cron_start_delay_minutes = 30
    on_this_day_reminder_enabled = False


class Service:
    async def generate(self, *_args, **_kwargs):
        return None


class V06WebTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.storage = DiaryStorage(Path(self.temp.name))
        self.storage.write_diary_data("2025-01-01", "# private markdown\n", {
            "date": "2025-01-01", "title": "Old", "projects": ["Old"], "people": ["Alice"],
            "topics": [], "events": [{"summary": "Private event", "memory_ids": [], "facts": ["Private fact"]}],
        })
        self.api = web_api.DiaryWebApi(Context(), Config(), self.storage, Service())
        self.previous_request = web_api.request

    def tearDown(self):
        web_api.request = self.previous_request
        self.temp.cleanup()

    def request(self, **kwargs):
        web_api.request = Request(**kwargs)

    async def test_management_routes_fail_closed_without_dashboard_identity(self):
        for handler in (self.api.corrections_list, self.api.revisions, self.api.archive_export, self.api.integrity_check, self.api.lifecycle):
            self.request(username="")
            self.assertEqual((await handler())["status_code"], 401)

    async def test_correction_rejects_illegal_or_ambiguous_parameters_without_mutation(self):
        before = self.storage.load_metadata("2025-01-01")
        self.request(payload={"date": "../2025-01-01", "field": "projects", "old_value": "Old", "new_value": "New"})
        self.assertEqual((await self.api.correction())["status_code"], 400)
        self.request(payload={"date": "2025-01-01", "field": "projects", "old_value": "missing", "new_value": "New"})
        self.assertEqual((await self.api.correction())["status_code"], 400)
        self.assertEqual(self.storage.load_metadata("2025-01-01"), before)

    async def test_correction_revision_and_rollback_are_current_fact_only(self):
        self.request(payload={"date": "2025-01-01", "field": "projects", "old_value": "Old", "new_value": "New"})
        result = await self.api.correction()
        self.assertEqual(result["correction"]["status"], "active")
        self.assertEqual(self.storage.load_metadata("2025-01-01")["projects"], ["New"])
        self.request(query={"date": "2025-01-01"})
        revisions = await self.api.revisions()
        current = revisions["current_revision_id"]
        self.assertTrue(current.startswith("rev_"))
        self.request(payload={"date": "2025-01-01", "revision_id": "rev_00000000000000000000000000000000"})
        self.assertEqual((await self.api.rollback())["status_code"], 400)

    async def test_archive_endpoints_expose_no_paths_or_raw_errors(self):
        self.request(payload={})
        archive = (await self.api.archive_export())["archive"]
        self.assertNotIn(str(self.storage.root), archive)
        self.request(query={"archive": archive})
        verified = await self.api.archive_verify()
        self.assertTrue(verified["valid"])
        self.request(query={"archive": "..\\private.zip"})
        rejected = await self.api.archive_verify()
        self.assertEqual(rejected["status_code"], 400)
        self.assertNotIn("private", rejected["error"])

    async def test_archive_restore_hides_snapshot_path_and_rejects_symlinks(self):
        self.request(payload={})
        archive = (await self.api.archive_export())["archive"]
        self.request(payload={"archive": archive, "dry_run": False})
        restored = await self.api.archive_restore()
        self.assertTrue(restored["pre_restore_snapshot"].endswith(".zip"))
        self.assertNotIn(str(self.storage.root), restored["pre_restore_snapshot"])
        link = self.api.archives.export_root / "diary-export-20250101T000000000000Z.zip"
        target = self.api.archives.export_root / archive
        try:
            link.symlink_to(target)
        except (NotImplementedError, OSError):
            self.skipTest("symlink creation is unavailable")
        self.request(query={"archive": link.name})
        self.assertEqual((await self.api.archive_verify())["status_code"], 400)

    async def test_lifecycle_and_reflection_validation_are_bounded(self):
        self.request(query={"field": "projects", "value": "Old"})
        self.assertEqual((await self.api.lifecycle())["lifecycle"]["first_seen"], "2025-01-01")
        self.request(query={"field": "projects", "value": "x" * 201})
        self.assertEqual((await self.api.lifecycle())["status_code"], 400)
        self.request(query={"kind": "daily", "period": "2025-01-01"})
        self.assertEqual((await self.api.reflections_list())["status_code"], 400)

    async def test_reflection_generation_keeps_monthly_and_yearly_routes(self):
        self.request(payload={"kind": "monthly", "period": "2025-01", "force": False})
        self.assertEqual((await self.api.reflection_generate())["error"], "generation_provider_id is not configured")

    def test_page_assets_render_untrusted_data_as_text_nodes(self):
        root = Path("pages/diary-manager")
        source = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.js"))
        self.assertNotIn("innerHTML", source)
        self.assertNotIn("localStorage", source)
        self.assertIn("textContent", source)
        self.assertIn(".download(", (root / "api.js").read_text(encoding="utf-8"))
        self.assertIn("event_id", (root / "v06.js").read_text(encoding="utf-8"))
        self.assertIn("revisionDiff", (root / "v06.js").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

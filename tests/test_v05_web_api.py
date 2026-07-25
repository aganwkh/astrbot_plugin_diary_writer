import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from diary.storage import DiaryStorage, atomic_write_json
import diary.web_api as web_api


class Request:
    def __init__(self, username="admin", query=None, payload=None):
        self.username = username
        self.query = query or {}
        self.payload = payload if payload is not None else {}

    async def json(self, default=None):
        return self.payload if self.payload is not None else default


class Context:
    def __init__(self):
        self.routes = []
        self.providers = []

    def register_web_api(self, *route):
        self.routes.append(route)

    def get_provider_by_id(self, provider_id):
        self.providers.append(provider_id)
        return object()


class Config:
    can_auto_write = True
    generation_provider_id = ""


class Service:
    def __init__(self):
        self.calls = []

    async def generate(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return "/daily.md"


class Reviews(Service):
    pass


class WebApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.storage = DiaryStorage(Path(self.temp.name))
        self.context = Context()
        self.service, self.reviews = Service(), Reviews()
        self.api = web_api.DiaryWebApi(self.context, Config(), self.storage, self.service, self.reviews)
        self.previous_request = web_api.request

    def tearDown(self):
        web_api.request = self.previous_request
        self.temp.cleanup()

    def request(self, **kwargs):
        web_api.request = Request(**kwargs)

    async def test_all_routes_are_registered_under_plugin_local_namespace(self):
        self.api.register()
        self.assertEqual(len(self.context.routes), 8)
        self.assertTrue(all(route[0].startswith("/astrbot_plugin_diary_writer/diary-manager/") for route in self.context.routes))

    async def test_missing_dashboard_identity_fails_closed(self):
        self.request(username="", query={"kind": "daily", "period": "2024-01-01"})
        result = await self.api.entry()
        self.assertEqual(result["status_code"], 401)

    async def test_invalid_period_range_and_limit_are_rejected(self):
        self.request(query={"kind": "weekly", "period": "2024-W54"})
        self.assertEqual((await self.api.entry())["status_code"], 400)
        self.request(query={"from": "2024-01-01", "to": "2026-01-01"})
        self.assertEqual((await self.api.calendar())["status_code"], 400)
        self.request(query={"q": "topic", "limit": "101"})
        self.assertEqual((await self.api.search())["status_code"], 400)

    async def test_read_routes_do_not_write_files_or_call_generation(self):
        atomic_write_json(self.storage.metadata_path("2024-01-01"), {
            "date": "2024-01-01", "title": "<unsafe>", "topics": ["AstrBot"], "projects": ["Godot"],
            "people": ["Alice"], "events": [{"summary": "event"}], "highlights": [], "unresolved": [],
        })
        self.storage.diary_path("2024-01-01").parent.mkdir(parents=True, exist_ok=True)
        self.storage.diary_path("2024-01-01").write_text("# <unsafe>\n", encoding="utf-8")
        before = {path.relative_to(self.storage.root): path.read_bytes() for path in self.storage.root.rglob("*") if path.is_file()}
        for handler, query in ((self.api.overview, {}), (self.api.calendar, {}), (self.api.entry, {"kind": "daily", "period": "2024-01-01"}), (self.api.search, {"q": "AstrBot"}), (self.api.entities, {"field": "people"}), (self.api.timeline, {"field": "projects", "value": "Godot"}), (self.api.trends, {})):
            self.request(query=query)
            await handler()
        after = {path.relative_to(self.storage.root): path.read_bytes() for path in self.storage.root.rglob("*") if path.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(self.service.calls, [])
        self.assertEqual(self.reviews.calls, [])

    async def test_calendar_defaults_to_a_bounded_summary_without_entities(self):
        today = date.today()
        recent = today.isoformat()
        old = (today - timedelta(days=web_api.MAX_CALENDAR_DAYS + 1)).isoformat()
        for value in (recent, old):
            atomic_write_json(self.storage.metadata_path(value), {
                "date": value, "title": "Private title", "topics": ["private topic"],
                "projects": ["private project"], "people": ["private person"],
                "mood": "private mood", "mood_score": 5, "events": [{"summary": "event"}],
            })
        self.request()
        result = await self.api.calendar()
        self.assertEqual(result["from"], (today - timedelta(days=web_api.MAX_CALENDAR_DAYS - 1)).isoformat())
        self.assertEqual(result["to"], recent)
        self.assertEqual([entry["date"] for entry in result["entries"]], [recent])
        self.assertEqual(set(result["entries"][0]), {"date", "title", "event_count"})

    async def test_generate_requires_configured_provider_without_umo_fallback(self):
        self.request(payload={"kind": "daily", "period": "2024-01-01", "force": False})
        result = await self.api.generate()
        self.assertEqual(result["status_code"], 400)
        self.assertEqual(self.context.providers, [])
        self.assertEqual(self.service.calls, [])

    async def test_generate_rejects_invalid_json_without_calling_provider(self):
        class InvalidJsonRequest(Request):
            async def json(self, default=None):
                raise ValueError("invalid JSON payload")

        self.api.config.generation_provider_id = "configured"
        web_api.request = InvalidJsonRequest()
        result = await self.api.generate()
        self.assertEqual(result["status_code"], 400)
        self.assertEqual(result["error"], "JSON object required")
        self.assertEqual(self.context.providers, [])
        self.assertEqual(self.service.calls, [])

    async def test_daily_generation_uses_configured_provider_and_force(self):
        self.api.config.generation_provider_id = "configured"
        called = []

        async def after_daily(*args):
            called.append(args)

        self.api.after_daily = after_daily
        self.request(payload={"kind": "daily", "period": "2024-01-01", "force": True})
        result = await self.api.generate()
        self.assertEqual(result, {"kind": "daily", "period": "2024-01-01", "generated": True})
        self.assertEqual(self.context.providers, ["configured"])
        self.assertTrue(self.service.calls[0][1]["force"])
        self.assertEqual(called[0][2], "daily_rewritten")

    async def test_existing_non_force_daily_does_not_stale_derived_reviews(self):
        from diary.config import DiaryConfig
        from diary.reviews import ReviewService
        from diary.service import DiaryService
        from tests.test_v05_yearly import Provider, daily

        class UnusedSource:
            def read_day(self, *_args, **_kwargs):
                raise AssertionError("existing daily must not read LivingMemory")

        daily(self.storage, "2024-01-01")
        self.storage.diary_path("2024-01-01").parent.mkdir(parents=True, exist_ok=True)
        self.storage.diary_path("2024-01-01").write_text("# existing\n", encoding="utf-8")
        reviews = ReviewService(self.storage)
        for kind, period in (("weekly", "2024-W01"), ("monthly", "2024-01"), ("yearly", "2024")):
            self.assertTrue(await reviews.generate(kind, period, Provider()))
        callbacks = []

        async def after_daily(*args):
            callbacks.append(args)

        self.api.service = DiaryService(DiaryConfig(), self.storage, UnusedSource())
        self.api.reviews, self.api.after_daily = reviews, after_daily
        self.api.config.generation_provider_id = "configured"
        self.request(payload={"kind": "daily", "period": "2024-01-01", "force": False})
        self.assertTrue((await self.api.generate())["generated"])
        self.assertEqual(callbacks, [])
        self.assertTrue(all(not self.storage.load_review_metadata(kind, period)["summary_stale"] for kind, period in (("weekly", "2024-W01"), ("monthly", "2024-01"), ("yearly", "2024"))))

    async def test_overview_redacts_raw_generation_errors(self):
        atomic_write_json(self.storage.state_path, {
            "pending_date": "2024-01-01", "stage": "failed", "retry_count": 2,
            "last_error": "token=secret C:\\private\\diary.md", "updated_at": "now", "last_success_at": "then",
        })
        atomic_write_json(self.storage.review_state_path, {
            "pending_period": "2024", "stage": "failed", "last_error": "provider says secret",
            "entries": {"yearly:2024": {"pending_period": "2024", "stage": "failed", "last_error": "/private/path"}},
        })
        self.request()
        result = await self.api.overview()
        rendered = repr(result)
        self.assertNotIn("secret", rendered)
        self.assertNotIn("private", rendered)
        self.assertEqual(result["generation_state"], {
            "pending_date": "2024-01-01", "stage": "failed", "retry_count": 2,
            "updated_at": "now", "last_success_at": "then", "failed": True,
        })
        self.assertTrue(result["review_generation_state"]["failed"])
        self.assertEqual(result["review_generation_state"]["entries"][0]["pending_period"], "2024")

    async def test_generate_failure_does_not_echo_provider_exception(self):
        class FailingService(Service):
            async def generate(self, *_args, **_kwargs):
                raise RuntimeError("token=secret C:\\private\\trace")

        self.api.service = FailingService()
        self.api.config.generation_provider_id = "configured"
        self.request(payload={"kind": "daily", "period": "2024-01-01", "force": False})
        result = await self.api.generate()
        self.assertEqual(result["status_code"], 500)
        self.assertNotIn("secret", result["error"])
        self.assertNotIn("private", result["error"])


if __name__ == "__main__":
    unittest.main()

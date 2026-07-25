import asyncio
import importlib.util
import json
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from diary.models import SourceMemory


class Source:
    def read_day(self, _day, limit=80):
        return [SourceMemory("real-id", datetime.now(), "A real source fact.")]


class Provider:
    def __init__(self):
        self.calls = 0

    async def text_chat(self, **_kwargs):
        self.calls += 1
        return type("Response", (), {"completion_text": json.dumps({
            "markdown": "# private diary\n\nA real source fact.",
            "title": "private diary",
            "events": [{"summary": "fact", "memory_ids": ["real-id"], "facts": ["A real source fact."]}],
        })})()


class CronManager:
    def __init__(self):
        self.jobs = []
        self.existing_jobs = []
        self.deleted = []

    async def list_jobs(self):
        return self.existing_jobs

    async def delete_job(self, job_id):
        self.deleted.append(job_id)

    async def add_basic_job(self, **kwargs):
        self.jobs.append(kwargs)


class Context:
    def __init__(self):
        self.cron_manager = CronManager()
        self.web_routes = []

    def register_web_api(self, *route):
        self.web_routes.append(route)

    def get_provider_by_id(self, _provider_id):
        return Provider()

    def get_using_provider(self, umo):
        return Provider()


class Event:
    def __init__(self, sender="1", origin="qq:FriendMessage:1"):
        self.sender = sender
        self.unified_msg_origin = origin

    def get_sender_id(self):
        return self.sender

    def plain_result(self, text):
        return text


def load_plugin(data_root: Path):
    logger = types.SimpleNamespace(info=lambda *_: None, warning=lambda *_: None, error=lambda *_: None)
    filter_api = types.SimpleNamespace(
        EventMessageType=types.SimpleNamespace(ALL="all", PRIVATE_MESSAGE="private_message"),
        event_message_type=lambda _kind: lambda function: function,
        command=lambda _name: lambda function: function,
    )
    api = types.ModuleType("astrbot.api")
    api.logger = logger
    event = types.ModuleType("astrbot.api.event")
    event.AstrMessageEvent = Event
    event.filter = filter_api
    star = types.ModuleType("astrbot.api.star")

    class Star:
        def __init__(self, context): self.context = context

    star.Context = Context
    star.Star = Star
    star.register = lambda *_args: lambda cls: cls
    path_api = types.ModuleType("astrbot.core.utils.astrbot_path")
    path_api.get_astrbot_data_path = lambda: str(data_root)
    modules = {
        "astrbot": types.ModuleType("astrbot"), "astrbot.api": api, "astrbot.api.event": event,
        "astrbot.api.star": star, "astrbot.core": types.ModuleType("astrbot.core"),
        "astrbot.core.utils": types.ModuleType("astrbot.core.utils"), "astrbot.core.utils.astrbot_path": path_api,
    }
    spec = importlib.util.spec_from_file_location("offline_main", Path("main.py"))
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


async def collect(generator):
    return [item async for item in generator]


class OfflinePluginSmokeTests(unittest.IsolatedAsyncioTestCase):
    async def test_initialize_registers_original_cron_windows_only_when_enabled(self):
        with tempfile.TemporaryDirectory() as temp:
            plugin_module = load_plugin(Path(temp))
            enabled_context = Context()
            enabled_context.cron_manager.existing_jobs = [types.SimpleNamespace(name="DiaryWriter_previous", job_id="old"), types.SimpleNamespace(name="Other", job_id="keep")]
            enabled = plugin_module.DiaryWriterPlugin(enabled_context, {"owner_ids": ["1"], "auto_write_enabled": True})
            await enabled.initialize()
            self.assertEqual([job["cron_expression"] for job in enabled_context.cron_manager.jobs], ["*/10 0-3 * * *", "0 4 * * *"])
            self.assertEqual(enabled_context.cron_manager.deleted, ["old"])
            self.assertEqual(len(enabled_context.web_routes), 8)

            disabled_context = Context()
            disabled = plugin_module.DiaryWriterPlugin(disabled_context, {"owner_ids": ["1"], "auto_write_enabled": False})
            await disabled.initialize()
            self.assertEqual(disabled_context.cron_manager.jobs, [])

    async def test_cron_obeys_inactivity_and_four_am_fallback_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            plugin_module = load_plugin(Path(temp))
            plugin = plugin_module.DiaryWriterPlugin(Context(), {"owner_ids": ["1"], "inactive_minutes": 90, "fallback_inactive_minutes": 60})
            plugin.service.source = Source()
            provider = Provider()

            async def get_provider(): return provider
            plugin._provider = get_provider
            plugin.storage.save_activity((datetime.now() - timedelta(minutes=89)).isoformat())
            await plugin._cron()
            self.assertEqual(provider.calls, 0)

            plugin.storage.save_activity((datetime.now() - timedelta(minutes=91)).isoformat())
            await plugin._cron()
            self.assertEqual(provider.calls, 1)

        with tempfile.TemporaryDirectory() as temp:
            plugin_module = load_plugin(Path(temp))
            plugin = plugin_module.DiaryWriterPlugin(Context(), {"owner_ids": ["1"], "fallback_inactive_minutes": 60})
            plugin.service.source = Source()
            provider = Provider()

            async def get_provider(): return provider
            plugin._provider = get_provider
            plugin.storage.save_activity((datetime.now() - timedelta(minutes=61)).isoformat())
            await plugin._fallback()
            self.assertEqual(provider.calls, 1)

    async def test_private_commands_do_not_leak_body_to_groups_or_unauthorized_users(self):
        with tempfile.TemporaryDirectory() as temp:
            plugin_module = load_plugin(Path(temp))
            plugin = plugin_module.DiaryWriterPlugin(Context(), {"owner_ids": ["1"]})
            path = plugin.storage.diary_path("2026-07-25")
            path.parent.mkdir(parents=True)
            path.write_text("TOP SECRET BODY", encoding="utf-8")
            group = Event(origin="qq:GroupMessage:123")
            private = Event()
            stranger = Event(sender="2")

            self.assertNotIn("TOP SECRET BODY", "".join(await collect(plugin.view(group, "2026-07-25"))))
            self.assertNotIn("TOP SECRET BODY", "".join(await collect(plugin.preview(group))))
            self.assertNotIn("TOP SECRET BODY", "".join(await collect(plugin.backfill(group, "2026-07-25"))))
            self.assertNotIn("TOP SECRET BODY", "".join(await collect(plugin.rewrite(group, "2026-07-25"))))
            self.assertNotIn("TOP SECRET BODY", "".join(await collect(plugin.ask(group, "secret"))))
            self.assertNotIn("TOP SECRET BODY", "".join(await collect(plugin.on_this_day(group))))
            self.assertNotIn("TOP SECRET BODY", "".join(await collect(plugin.project(group, "Godot"))))
            self.assertNotIn("TOP SECRET BODY", "".join(await collect(plugin.topic(group, "AstrBot"))))
            self.assertNotIn("TOP SECRET BODY", "".join(await collect(plugin.backfill_weekly(group, "2025-W30"))))
            self.assertNotIn("TOP SECRET BODY", "".join(await collect(plugin.rewrite_weekly(group, "2025-W30"))))
            self.assertNotIn("TOP SECRET BODY", "".join(await collect(plugin.backfill_monthly(group, "2025-07"))))
            self.assertNotIn("TOP SECRET BODY", "".join(await collect(plugin.rewrite_monthly(group, "2025-07"))))
            self.assertNotIn("TOP SECRET BODY", "".join(await collect(plugin.backfill_yearly(group, "2025"))))
            self.assertNotIn("TOP SECRET BODY", "".join(await collect(plugin.rewrite_yearly(group, "2025"))))
            self.assertEqual(await collect(plugin.view(stranger, "2026-07-25")), [])
            self.assertIn("TOP SECRET BODY", "".join(await collect(plugin.view(private, "2026-07-25"))))

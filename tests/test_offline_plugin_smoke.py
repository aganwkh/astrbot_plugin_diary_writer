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

from diary.models import GenerationState, SourceMemory


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

    def get_config(self, umo=None):
        return {"provider_settings": {"default_personality": "default-persona"}}


class Event:
    def __init__(self, sender="1", origin="qq:FriendMessage:1"):
        self.sender = sender
        self.unified_msg_origin = origin

    def get_sender_id(self):
        return self.sender

    def plain_result(self, text):
        return text


def load_plugin(data_root: Path, module_name: str = "offline_main"):
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
    if "." in module_name:
        package_names = ("data", "data.plugins", "data.plugins.astrbot_plugin_diary_writer")
        for package_name in package_names:
            package = types.ModuleType(package_name)
            package.__path__ = [str(Path.cwd())]
            modules[package_name] = package
    spec = importlib.util.spec_from_file_location(module_name, Path("main.py"))
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


async def collect(generator):
    return [item async for item in generator]


class OfflinePluginSmokeTests(unittest.IsolatedAsyncioTestCase):
    async def test_diary_resolves_the_selected_astrbot_conversation_persona(self):
        with tempfile.TemporaryDirectory() as temp:
            plugin_module = load_plugin(Path(temp))
            context = Context()

            class Conversations:
                async def get_curr_conversation_id(self, umo): return "conversation"
                async def get_conversation(self, umo, conversation_id):
                    return types.SimpleNamespace(persona_id="selected-persona")

            class Personas:
                async def resolve_selected_persona(self, **kwargs):
                    self.kwargs = kwargs
                    return "selected-persona", {"prompt": "SELECTED ASTRBOT PERSONA"}, None, False
                async def get_default_persona_v3(self):
                    return {"prompt": "DEFAULT ASTRBOT PERSONA"}

            context.conversation_manager = Conversations()
            context.persona_manager = Personas()
            plugin = plugin_module.DiaryWriterPlugin(context, {"owner_ids": ["1"]})
            prompt = await plugin._astrbot_persona_prompt(["qq:FriendMessage:1"])
            self.assertEqual(prompt, "SELECTED ASTRBOT PERSONA")
            self.assertEqual(context.persona_manager.kwargs["conversation_persona_id"], "selected-persona")

    async def test_entrypoint_loads_under_astrbot_package_namespace(self):
        with tempfile.TemporaryDirectory() as temp:
            plugin_module = load_plugin(
                Path(temp),
                "data.plugins.astrbot_plugin_diary_writer.main",
            )
            plugin = plugin_module.DiaryWriterPlugin(
                Context(),
                {"owner_ids": ["1"], "auto_write_enabled": False},
            )
            await plugin.initialize()

    async def test_initialize_registers_original_cron_windows_only_when_enabled(self):
        with tempfile.TemporaryDirectory() as temp:
            plugin_module = load_plugin(Path(temp))
            enabled_context = Context()
            enabled_context.cron_manager.existing_jobs = [types.SimpleNamespace(name="DiaryWriter_previous", job_id="old"), types.SimpleNamespace(name="Other", job_id="keep")]
            enabled = plugin_module.DiaryWriterPlugin(enabled_context, {"owner_ids": ["1"], "auto_write_enabled": True})
            await enabled.initialize()
            self.assertEqual([job["cron_expression"] for job in enabled_context.cron_manager.jobs], ["*/10 0-3 * * *", "0 4 * * *", "10 4 * * *"])
            self.assertEqual(enabled_context.cron_manager.deleted, ["old"])
            self.assertGreaterEqual(len(enabled_context.web_routes), 8)

            disabled_context = Context()
            disabled_context.cron_manager.existing_jobs = [
                types.SimpleNamespace(name="DiaryWriter_previous", job_id="old"),
                types.SimpleNamespace(name="Other", job_id="keep"),
            ]
            disabled = plugin_module.DiaryWriterPlugin(disabled_context, {"owner_ids": ["1"], "auto_write_enabled": False})
            await disabled.initialize()
            self.assertEqual(disabled_context.cron_manager.jobs, [])
            self.assertEqual(disabled_context.cron_manager.deleted, ["old"])

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

    async def test_activity_counts_only_authorized_non_command_private_messages(self):
        with tempfile.TemporaryDirectory() as temp:
            plugin_module = load_plugin(Path(temp))
            plugin = plugin_module.DiaryWriterPlugin(Context(), {"owner_ids": ["1"]})
            private = Event(); private.message_str = "第一句"
            second = Event(); second.message_str = "第二句"
            third = Event(); third.message_str = "第三句"
            group = Event(origin="qq:GroupMessage:1"); group.message_str = "群聊"
            stranger = Event(sender="2"); stranger.message_str = "陌生人"
            command = Event(); command.message_str = "/查看日记"
            for event in (private, second, third, group, stranger, command):
                await plugin.on_user_message(event)
            activity = plugin.storage.load_daily_activity(datetime.now().date().isoformat())
            self.assertEqual(activity["round_count"], 3)
            self.assertEqual([item["user_text"] for item in activity["conversation_sources"]], ["第一句", "第二句"])

    async def test_finalization_fills_only_post_effective_missing_days_and_is_idempotent(self):
        class EmptySource:
            def read_day(self, *_args, **_kwargs): return []
            def read_range(self, *_args, **_kwargs): return []
            def read_before(self, *_args, **_kwargs): return []

        with tempfile.TemporaryDirectory() as temp:
            plugin_module = load_plugin(Path(temp))
            plugin = plugin_module.DiaryWriterPlugin(Context(), {"owner_ids": ["1"]})
            plugin.service.source = EmptySource()
            provider = Provider()

            async def get_provider(_event=None): return provider
            plugin._provider = get_provider
            yesterday = (datetime.now() - timedelta(days=1)).date()
            plugin.storage.save_daily_finalization_state({"effective_date": yesterday.isoformat()})
            await plugin._daily_finalization()
            self.assertTrue(plugin.storage.has_diary(yesterday.isoformat()))
            self.assertEqual(provider.calls, 1)
            await plugin._daily_finalization()
            self.assertEqual(provider.calls, 1)

        with tempfile.TemporaryDirectory() as temp:
            plugin_module = load_plugin(Path(temp))
            plugin = plugin_module.DiaryWriterPlugin(Context(), {"owner_ids": ["1"]})
            plugin.storage.save_daily_finalization_state({"effective_date": datetime.now().date().isoformat()})
            await plugin._daily_finalization()
            self.assertFalse(plugin.storage.has_any_diary((datetime.now() - timedelta(days=1)).date().isoformat()))
            self.assertEqual(plugin.storage.load_generation_state().stage, "idle")

        with tempfile.TemporaryDirectory() as temp:
            plugin_module = load_plugin(Path(temp))
            plugin = plugin_module.DiaryWriterPlugin(Context(), {"owner_ids": ["1"]})
            yesterday = (datetime.now() - timedelta(days=1)).date()
            plugin.storage.write_diary_data(yesterday.isoformat(), "# existing\n", {"date": yesterday.isoformat()})
            plugin.storage.save_daily_finalization_state({"effective_date": yesterday.isoformat()})
            original = GenerationState(stage="idle", last_success_at="kept", updated_at="kept")
            plugin.storage.save_generation_state(original)

            async def no_provider(_event=None): return None
            plugin._provider = no_provider
            await plugin._daily_finalization()
            state = plugin.storage.load_generation_state()
            self.assertEqual((state.stage, state.last_success_at, state.updated_at), ("idle", "kept", "kept"))

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

    async def test_restore_cannot_split_daily_follow_up_or_state_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            plugin_module = load_plugin(Path(temp))
            plugin = plugin_module.DiaryWriterPlugin(Context(), {"owner_ids": ["1"], "on_this_day_reminder_enabled": True})
            plugin.service.source = Source()
            provider = Provider()

            async def get_provider(_event=None): return provider
            plugin._provider = get_provider
            review_started, release_review, restore_entered = asyncio.Event(), asyncio.Event(), asyncio.Event()
            original_follow_up = plugin.reviews._after_daily_written_unlocked

            async def delayed_follow_up(*args):
                review_started.set()
                await release_review.wait()
                return await original_follow_up(*args)

            plugin.reviews._after_daily_written_unlocked = delayed_follow_up
            write_task = asyncio.create_task(collect(plugin.backfill(Event(), "2025-07-27")))
            await review_started.wait()

            async def restore_once():
                async with plugin_module.GLOBAL_MAINTENANCE_GATE.restore():
                    restore_entered.set()

            restore_task = asyncio.create_task(restore_once())
            await asyncio.sleep(0)
            self.assertFalse(restore_entered.is_set())
            release_review.set()
            await write_task
            await restore_task
            self.assertTrue(plugin.storage.has_any_diary("2025-07-27"))

            today = datetime.now().date()
            past = today.replace(year=today.year - 1)
            from diary.storage import atomic_write_json
            atomic_write_json(plugin.storage.metadata_path(past.isoformat()), {"date": past.isoformat(), "title": "history", "events": []})
            event = Event(); event.message_str = "ordinary private message"
            async with plugin_module.GLOBAL_MAINTENANCE_GATE.restore():
                activity_task = asyncio.create_task(plugin.on_user_message(event))
                reminder_task = asyncio.create_task(collect(plugin.on_this_day_reminder(event)))
                await asyncio.sleep(0)
                self.assertFalse(plugin.storage.load_activity())
                self.assertFalse(plugin.storage.reminder_state_path.exists())
            await activity_task
            await reminder_task
            self.assertTrue(plugin.storage.load_activity())
            self.assertTrue(plugin.storage.reminder_state_path.exists())

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

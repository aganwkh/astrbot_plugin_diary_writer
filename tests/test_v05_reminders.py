import asyncio
import tempfile
import unittest
from datetime import date
from pathlib import Path

from diary.storage import DiaryStorage, atomic_write_json
from test_offline_plugin_smoke import Context, Event, collect, load_plugin


def historical_daily(storage: DiaryStorage, today: date) -> None:
    for offset in range(1, 6):
        try:
            past = today.replace(year=today.year - offset)
            break
        except ValueError:
            continue
    else:
        raise AssertionError("could not find a prior matching calendar day")
    atomic_write_json(storage.metadata_path(past.isoformat()), {
        "date": past.isoformat(), "title": "去年今天", "events": [{"summary": "真实记录"}],
    })


class OnThisDayReminderTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_enabled_authorized_ordinary_private_message_can_remind_once(self):
        with tempfile.TemporaryDirectory() as temp:
            module = load_plugin(Path(temp))
            plugin = module.DiaryWriterPlugin(Context(), {"owner_ids": ["1"], "on_this_day_reminder_enabled": True})
            historical_daily(plugin.storage, date.today())
            event = Event(); event.message_str = "今天怎么样"

            first = await collect(plugin.on_this_day_reminder(event))
            second = await collect(plugin.on_this_day_reminder(event))

            self.assertIn("去年今天", "".join(first))
            self.assertEqual(second, [])
            self.assertEqual(plugin.storage.load_reminder_state()["date"], date.today().isoformat())

    async def test_concurrent_private_messages_still_yield_one_reminder(self):
        with tempfile.TemporaryDirectory() as temp:
            module = load_plugin(Path(temp))
            plugin = module.DiaryWriterPlugin(Context(), {"owner_ids": ["1"], "on_this_day_reminder_enabled": True})
            historical_daily(plugin.storage, date.today())
            events = [Event() for _ in range(3)]
            for event in events:
                event.message_str = "普通私聊"

            results = await asyncio.gather(*(collect(plugin.on_this_day_reminder(event)) for event in events))

            self.assertEqual(sum(bool(result) for result in results), 1)

    async def test_enabled_without_history_has_no_output_or_state(self):
        with tempfile.TemporaryDirectory() as temp:
            module = load_plugin(Path(temp))
            plugin = module.DiaryWriterPlugin(Context(), {"owner_ids": ["1"], "on_this_day_reminder_enabled": True})
            event = Event(); event.message_str = "普通私聊"

            self.assertEqual(await collect(plugin.on_this_day_reminder(event)), [])
            self.assertFalse(plugin.storage.reminder_state_path.exists())

    async def test_reminder_listener_does_not_suppress_authorized_activity_tracking(self):
        with tempfile.TemporaryDirectory() as temp:
            module = load_plugin(Path(temp))
            plugin = module.DiaryWriterPlugin(Context(), {"owner_ids": ["1"], "on_this_day_reminder_enabled": True})
            event = Event(); event.message_str = "普通私聊"

            await plugin.on_user_message(event)
            self.assertTrue(plugin.storage.load_activity())

    async def test_disabled_commands_groups_and_strangers_never_remind(self):
        with tempfile.TemporaryDirectory() as temp:
            module = load_plugin(Path(temp))
            disabled = module.DiaryWriterPlugin(Context(), {"owner_ids": ["1"]})
            historical_daily(disabled.storage, date.today())
            ordinary = Event(); ordinary.message_str = "你好"
            self.assertEqual(await collect(disabled.on_this_day_reminder(ordinary)), [])
            self.assertFalse(disabled.storage.reminder_state_path.exists())

            plugin = module.DiaryWriterPlugin(Context(), {"owner_ids": ["1"], "on_this_day_reminder_enabled": True})
            historical_daily(plugin.storage, date.today())
            for event in (Event(origin="qq:GroupMessage:1"), Event(sender="2"), Event()):
                event.message_str = "/那年今日" if event.sender == "1" and "Group" not in event.unified_msg_origin else "普通消息"
                self.assertEqual(await collect(plugin.on_this_day_reminder(event)), [])
            self.assertFalse(plugin.storage.reminder_state_path.exists())

    def test_reminder_state_round_trips_atomically(self):
        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp))
            storage.save_reminder_state({"date": "2026-07-25", "recipient_id": "1", "reminded_at": "now"})
            self.assertEqual(storage.load_reminder_state()["recipient_id"], "1")

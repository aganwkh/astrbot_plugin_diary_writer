import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path

from diary.config import DiaryConfig
from diary.models import SourceMemory
from diary.service import DiaryService
from diary.storage import DiaryStorage


class ActivityTrackerTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_two_private_messages_are_persisted_but_later_messages_only_increment_count(self):
        from diary.activity import DailyActivityTracker

        with tempfile.TemporaryDirectory() as temporary:
            storage = DiaryStorage(Path(temporary))
            tracker = DailyActivityTracker(storage)
            target = date(2026, 7, 25)
            await tracker.record(target, "2026-07-25T09:00:00+00:00", "第一句", "qq:FriendMessage:owner")
            await tracker.record(target, "2026-07-25T10:00:00+00:00", "第二句", "qq:FriendMessage:owner")
            await tracker.record(target, "2026-07-25T11:00:00+00:00", "第三句不应持久化", "qq:FriendMessage:owner")

            saved = DiaryStorage(Path(temporary)).load_daily_activity("2026-07-25")
            self.assertEqual(saved["round_count"], 3)
            self.assertEqual([item["user_text"] for item in saved["conversation_sources"]], ["第一句", "第二句"])
            self.assertEqual(saved["private_session_ids"], ["qq:FriendMessage:owner"])

    async def test_entry_modes_and_historical_weights_keep_private_memories_and_cooldown_is_soft(self):
        from diary.activity import classify_entry_type, historical_weight, select_historical_memories

        target = date(2026, 7, 25)
        self.assertEqual(classify_entry_type(0, 99), "low_activity")
        self.assertEqual(classify_entry_type(2, 99), "low_activity")
        self.assertEqual(classify_entry_type(3, 0), "sparse")
        self.assertEqual(classify_entry_type(3, 2), "sparse")
        self.assertEqual(classify_entry_type(3, 3), "normal")

        recent = SourceMemory("recent", datetime(2026, 7, 20, tzinfo=timezone.utc), "近期私聊", importance=1, session_id="qq:FriendMessage:owner")
        old = SourceMemory("old", datetime(2025, 1, 1, tzinfo=timezone.utc), "旧私聊", importance=1, session_id="qq:FriendMessage:owner")
        high = SourceMemory("high", datetime(2026, 1, 1, tzinfo=timezone.utc), "高重要度", importance=8, session_id="qq:FriendMessage:owner")
        group = SourceMemory("group", datetime(2026, 7, 20, tzinfo=timezone.utc), "群聊", importance=99, session_id="qq:GroupMessage:owner")
        usage = {"recent": {"last_reflected_at": datetime(2026, 7, 24, tzinfo=timezone.utc).isoformat(), "reflection_count": 1}}

        self.assertGreater(historical_weight(high, target, {}), historical_weight(old, target, {}))
        self.assertGreater(historical_weight(old, target, {}), 0)
        self.assertLess(historical_weight(recent, target, usage), historical_weight(recent, target, {}))
        picked = select_historical_memories([recent, old, high, group], target, usage, {"qq:FriendMessage:owner"})
        self.assertGreaterEqual(len(picked), 1)
        self.assertLessEqual(len(picked), 3)
        self.assertNotIn("group", {item.memory_id for item in picked})

    async def test_low_activity_persists_sources_cools_only_used_memory_and_rewrite_keeps_candidates(self):
        class Source:
            def __init__(self):
                self.read_history = True

            def read_day(self, _day, limit=80):
                return []

            def read_range(self, *_args, **_kwargs):
                return []

            def read_before(self, _day, session_ids, limit=500):
                if not self.read_history:
                    raise AssertionError("rewrite must use saved low-activity sources")
                return [SourceMemory("old-memory", datetime(2026, 6, 1, tzinfo=timezone.utc), "以前的私聊", importance=2, session_id="qq:FriendMessage:owner")]

        class Provider:
            async def text_chat(self, **_kwargs):
                return type("Response", (), {"completion_text": json.dumps({
                    "markdown": "# 低活跃\n\n突然想起以前那件小事。",
                    "title": "低活跃",
                    "events": [],
                    "used_historical_memory_ids": ["old-memory"],
                })})()

        with tempfile.TemporaryDirectory() as temporary:
            storage = DiaryStorage(Path(temporary))
            storage.save_daily_activity("2026-07-25", {
                "date": "2026-07-25", "round_count": 1,
                "conversation_sources": [{"timestamp": "2026-07-25T09:00:00+00:00", "user_text": "今天很累"}],
                "private_session_ids": ["qq:FriendMessage:owner"],
            })
            source = Source()
            service = DiaryService(DiaryConfig.from_mapping({"owner_ids": ["owner"]}), storage, source)
            self.assertTrue(await service.generate(date(2026, 7, 25), Provider()))
            metadata = storage.load_metadata("2026-07-25")
            self.assertEqual(metadata["entry_type"], "low_activity")
            self.assertEqual(metadata["conversation_sources"][0]["user_text"], "今天很累")
            self.assertEqual(metadata["historical_memory_candidate_ids"], ["old-memory"])
            self.assertEqual(storage.load_reflection_usage()["old-memory"]["reflection_count"], 1)
            self.assertFalse(storage.daily_activity_path("2026-07-25").exists())

            source.read_history = False
            self.assertTrue(await service.generate(date(2026, 7, 25), Provider(), force=True))
            self.assertEqual(storage.load_metadata("2026-07-25")["historical_memory_candidate_ids"], ["old-memory"])

    async def test_failed_low_activity_generation_keeps_activity_for_retry(self):
        class Source:
            def read_day(self, _day, limit=80): return []
            def read_range(self, *_args, **_kwargs): return []
            def read_before(self, *_args, **_kwargs): return []

        class BrokenProvider:
            async def text_chat(self, **_kwargs):
                raise RuntimeError("offline")

        with tempfile.TemporaryDirectory() as temporary:
            storage = DiaryStorage(Path(temporary))
            storage.save_daily_activity("2026-07-25", {"date": "2026-07-25", "round_count": 0, "conversation_sources": [], "private_session_ids": []})
            service = DiaryService(DiaryConfig.from_mapping({"owner_ids": ["owner"], "provider_retry_count": 0}), storage, Source())
            self.assertIsNone(await service.generate(date(2026, 7, 25), BrokenProvider()))
            self.assertTrue(storage.daily_activity_path("2026-07-25").exists())

    async def test_sparse_uses_recent_private_context_without_random_history_and_normal_keeps_normal_prompt_path(self):
        class Source:
            def __init__(self, day): self.day = day; self.before_calls = 0; self.range_calls = 0
            def read_day(self, _day, limit=80): return self.day
            def read_range(self, *_args, **_kwargs): self.range_calls += 1; return []
            def read_before(self, *_args, **_kwargs): self.before_calls += 1; return []

        class Provider:
            def __init__(self): self.prompts = []
            async def text_chat(self, prompt, **_kwargs):
                self.prompts.append(prompt)
                return type("Response", (), {"completion_text": json.dumps({"markdown": "# d\n", "title": "d", "events": []})})()

        with tempfile.TemporaryDirectory() as temporary:
            storage = DiaryStorage(Path(temporary))
            storage.save_daily_activity("2026-07-25", {"date": "2026-07-25", "round_count": 3, "conversation_sources": [], "private_session_ids": ["qq:FriendMessage:owner"]})
            sparse_source = Source([])
            sparse_provider = Provider()
            self.assertTrue(await DiaryService(DiaryConfig.from_mapping({"owner_ids": ["owner"]}), storage, sparse_source).generate(date(2026, 7, 25), sparse_provider))
            self.assertEqual(storage.load_metadata("2026-07-25")["entry_type"], "sparse")
            self.assertEqual(sparse_source.before_calls, 0)
            self.assertEqual(sparse_source.range_calls, 1)
            self.assertIn('"entry_type": "sparse"', sparse_provider.prompts[0])

        with tempfile.TemporaryDirectory() as temporary:
            storage = DiaryStorage(Path(temporary))
            storage.save_daily_activity("2026-07-25", {"date": "2026-07-25", "round_count": 3, "conversation_sources": [], "private_session_ids": ["qq:FriendMessage:owner"]})
            memories = [SourceMemory(str(index), datetime(2026, 7, 25, index + 1, tzinfo=timezone.utc), f"事实{index}") for index in range(3)]
            normal_source = Source(memories)
            normal_provider = Provider()
            self.assertTrue(await DiaryService(DiaryConfig.from_mapping({"owner_ids": ["owner"]}), storage, normal_source).generate(date(2026, 7, 25), normal_provider))
            self.assertEqual(storage.load_metadata("2026-07-25")["entry_type"], "normal")
            self.assertEqual(normal_source.before_calls, 0)
            self.assertEqual(normal_source.range_calls, 0)
            self.assertNotIn('"entry_type": "normal"', normal_provider.prompts[0])

    async def test_archive_round_trips_v11_activity_and_reflection_state(self):
        from diary.archives import ArchiveService

        with tempfile.TemporaryDirectory() as temporary:
            storage = DiaryStorage(Path(temporary))
            storage.save_daily_activity("2026-07-25", {"date": "2026-07-25", "round_count": 1, "conversation_sources": [], "private_session_ids": ["qq:FriendMessage:owner"]})
            storage.save_reflection_usage({"old-memory": {"last_reflected_at": "2026-07-25T00:00:00+00:00", "reflection_count": 1}})
            storage.save_daily_finalization_state({"effective_date": "2026-07-25"})
            archive = await ArchiveService(storage).export()
            manifest = ArchiveService(storage).verify(archive)
            names = {item["path"] for item in manifest["files"]}
            self.assertIn("daily_activity/2026-07-25.json", names)
            self.assertIn("reflection_usage.json", names)
            self.assertIn("daily_finalization_state.json", names)
            storage.delete_daily_activity("2026-07-25")
            storage.save_reflection_usage({})
            storage.save_daily_finalization_state({})
            await ArchiveService(storage).restore(archive)
            self.assertEqual(storage.load_daily_activity("2026-07-25")["round_count"], 1)
            self.assertEqual(storage.load_reflection_usage()["old-memory"]["reflection_count"], 1)
            self.assertEqual(storage.load_daily_finalization_state()["effective_date"], "2026-07-25")


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path

from diary.config import DiaryConfig
from diary.models import SourceMemory
from diary.prompts import ADAPTIVE_PROMPT_VERSION, NORMAL_PROMPT_VERSION
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

    async def test_entry_type_uses_livingmemory_count_only(self):
        from diary.activity import classify_entry_type, historical_weight, select_historical_memories

        target = date(2026, 7, 25)
        self.assertEqual(classify_entry_type(0), "low_activity")
        self.assertEqual(classify_entry_type(4), "low_activity")
        self.assertEqual(classify_entry_type(5), "normal")

        recent = SourceMemory("recent", datetime(2026, 7, 20, tzinfo=timezone.utc), "近期私聊", importance=1, session_id="qq:FriendMessage:owner")
        old = SourceMemory("old", datetime(2025, 1, 1, tzinfo=timezone.utc), "旧私聊", importance=1, session_id="qq:FriendMessage:owner")
        high = SourceMemory("high", datetime(2026, 1, 1, tzinfo=timezone.utc), "高重要度", importance=8, session_id="qq:FriendMessage:owner")
        group = SourceMemory("group", datetime(2026, 7, 20, tzinfo=timezone.utc), "群聊", importance=99, session_id="qq:GroupMessage:owner")
        usage = {"recent": {"last_reflected_at": datetime(2026, 7, 24, tzinfo=timezone.utc).isoformat(), "reflection_count": 1}}

        self.assertGreater(historical_weight(high, target, {}), historical_weight(old, target, {}))
        self.assertGreater(historical_weight(old, target, {}), 0)
        self.assertLess(historical_weight(recent, target, usage), historical_weight(recent, target, {}))
        picked = select_historical_memories([recent, old, high, group], target, usage, {"qq:FriendMessage:owner"})
        self.assertGreaterEqual(len(picked), 3)
        self.assertLessEqual(len(picked), 5)
        self.assertNotIn("group", {item.memory_id for item in picked})

    async def test_low_activity_persists_sources_cools_only_used_memory_and_rewrite_keeps_candidates(self):
        class Source:
            def __init__(self):
                self.read_history = True

            def read_day(self, _day, limit=80):
                return []

            def read_range(self, *_args, **_kwargs):
                if not self.read_history:
                    raise AssertionError("rewrite must use saved low-activity recent sources")
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
            original_historical_sources = metadata["historical_memory_sources"]
            self.assertEqual(storage.load_reflection_usage()["old-memory"]["reflection_count"], 1)
            self.assertFalse(storage.daily_activity_path("2026-07-25").exists())

            source.read_history = False
            self.assertTrue(await service.generate(date(2026, 7, 25), Provider(), force=True))
            rewritten = storage.load_metadata("2026-07-25")
            self.assertEqual(rewritten["entry_type"], "low_activity")
            self.assertEqual(rewritten["activity_round_count"], 1)
            self.assertEqual(rewritten["historical_memory_candidate_ids"], ["old-memory"])
            self.assertEqual(rewritten["historical_memory_sources"], original_historical_sources)

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

    async def test_low_activity_uses_history_when_livingmemory_is_sparse_and_normal_keeps_normal_prompt_path(self):
        class Source:
            def __init__(self, day, recent=None, history=None): self.day = day; self.recent = recent or []; self.history = history or []; self.before_calls = 0; self.range_calls = 0
            def read_day(self, _day, limit=80): return self.day
            def read_range(self, *_args, **_kwargs): self.range_calls += 1; return self.recent
            def read_before(self, *_args, **_kwargs): self.before_calls += 1; return self.history

        class Provider:
            def __init__(self): self.prompts = []
            async def text_chat(self, prompt, **_kwargs):
                self.prompts.append(prompt)
                return type("Response", (), {"completion_text": json.dumps({"markdown": "# d\n", "title": "d", "events": [], "used_historical_memory_ids": ["historical-1"]})})()

        with tempfile.TemporaryDirectory() as temporary:
            storage = DiaryStorage(Path(temporary))
            storage.save_daily_activity("2026-07-25", {"date": "2026-07-25", "round_count": 3, "conversation_sources": [], "private_session_ids": ["qq:FriendMessage:owner"]})
            history = [SourceMemory(f"historical-{index}", datetime(2026, 7, 20, index + 1, tzinfo=timezone.utc), f"历史事实{index}", session_id="qq:FriendMessage:owner") for index in range(1, 4)]
            low_source = Source([], history=history)
            low_provider = Provider()
            self.assertTrue(await DiaryService(DiaryConfig.from_mapping({"owner_ids": ["owner"]}), storage, low_source).generate(date(2026, 7, 25), low_provider))
            self.assertEqual(storage.load_metadata("2026-07-25")["entry_type"], "low_activity")
            self.assertEqual(low_source.before_calls, 1)
            self.assertEqual(low_source.range_calls, 1)
            self.assertIn('"entry_type": "low_activity"', low_provider.prompts[0])
            low_metadata = storage.load_metadata("2026-07-25")
            self.assertEqual(set(low_metadata["historical_memory_candidate_ids"]), {"historical-1", "historical-2", "historical-3"})
            self.assertEqual(low_metadata["historical_memory_used_ids"], ["historical-1"])
            self.assertEqual(low_metadata["prompt_version"], ADAPTIVE_PROMPT_VERSION)

        with tempfile.TemporaryDirectory() as temporary:
            storage = DiaryStorage(Path(temporary))
            storage.save_daily_activity("2026-07-25", {"date": "2026-07-25", "round_count": 3, "conversation_sources": [], "private_session_ids": ["qq:FriendMessage:owner"]})
            memories = [SourceMemory(str(index), datetime(2026, 7, 25, index + 1, tzinfo=timezone.utc), f"事实{index}") for index in range(5)]
            normal_source = Source(memories)
            normal_provider = Provider()
            self.assertTrue(await DiaryService(DiaryConfig.from_mapping({"owner_ids": ["owner"]}), storage, normal_source).generate(date(2026, 7, 25), normal_provider))
            self.assertEqual(storage.load_metadata("2026-07-25")["entry_type"], "normal")
            self.assertEqual(normal_source.before_calls, 0)
            self.assertEqual(normal_source.range_calls, 0)
            self.assertIn('"entry_type": "normal"', normal_provider.prompts[0])
            self.assertEqual(storage.load_metadata("2026-07-25")["prompt_version"], NORMAL_PROMPT_VERSION)

    async def test_rewrite_preserves_low_activity_contract_without_daily_activity(self):
        class Source:
            def __init__(self):
                self.allow_context_reads = True

            def read_day(self, _day, limit=80):
                return [SourceMemory("today", datetime(2026, 7, 25, 9, tzinfo=timezone.utc), "当天事实")]

            def read_range(self, *_args, **_kwargs):
                if not self.allow_context_reads:
                    raise AssertionError("low activity rewrite must reuse frozen sources")
                return []

            def read_before(self, *_args, **_kwargs):
                if not self.allow_context_reads:
                    raise AssertionError("low activity rewrite must reuse frozen sources")
                return [SourceMemory(f"historical-{index}", datetime(2026, 7, 20, index, tzinfo=timezone.utc), f"历史事实{index}", session_id="qq:FriendMessage:owner") for index in range(1, 4)]

        class Provider:
            async def text_chat(self, **_kwargs):
                return type("Response", (), {"completion_text": json.dumps({
                    "markdown": "# low\n", "title": "low", "events": [], "used_historical_memory_ids": ["historical-1"],
                })})()

        with tempfile.TemporaryDirectory() as temporary:
            storage = DiaryStorage(Path(temporary))
            storage.save_daily_activity("2026-07-25", {
                "round_count": 8,
                "conversation_sources": [{"timestamp": "2026-07-25T09:00:00+00:00", "user_text": "今天有一点事"}],
                "private_session_ids": ["qq:FriendMessage:owner"],
            })
            source = Source()
            service = DiaryService(DiaryConfig.from_mapping({"owner_ids": ["owner"]}), storage, source)
            self.assertTrue(await service.generate(date(2026, 7, 25), Provider()))
            original = storage.load_metadata("2026-07-25")
            self.assertEqual(original["entry_type"], "low_activity")
            self.assertEqual(original["activity_round_count"], 8)
            self.assertFalse(storage.daily_activity_path("2026-07-25").exists())

            source.allow_context_reads = False
            self.assertTrue(await service.generate(date(2026, 7, 25), Provider(), force=True))
            rewritten = storage.load_metadata("2026-07-25")
            self.assertEqual(rewritten["entry_type"], "low_activity")
            self.assertEqual(rewritten["activity_round_count"], 8)
            self.assertEqual(rewritten["conversation_sources"], original["conversation_sources"])
            self.assertEqual(rewritten["historical_memory_sources"], original["historical_memory_sources"])
            self.assertEqual(rewritten["private_session_ids"], original["private_session_ids"])

    async def test_rewrite_migrates_legacy_sparse_to_low_activity_and_keeps_chat_sources(self):
        class Source:
            def __init__(self): self.before_calls = 0
            def read_day(self, _day, limit=80): return []
            def read_range(self, *_args, **_kwargs): return []
            def read_before(self, *_args, **_kwargs):
                self.before_calls += 1
                return [SourceMemory(f"historical-{index}", datetime(2026, 7, 20, index, tzinfo=timezone.utc), f"历史事实{index}", session_id="qq:FriendMessage:owner") for index in range(1, 4)]

        class Provider:
            async def text_chat(self, **_kwargs):
                return type("Response", (), {"completion_text": json.dumps({
                    "markdown": "# low\n", "title": "low", "events": [], "used_historical_memory_ids": ["historical-1"],
                })})()

        with tempfile.TemporaryDirectory() as temporary:
            storage = DiaryStorage(Path(temporary))
            chat_sources = [{"timestamp": "2026-07-25T09:00:00+00:00", "user_text": "当天的原始聊天"}]
            storage.write_diary_data("2026-07-25", "# sparse\n", {
                "date": "2026-07-25", "title": "sparse", "entry_type": "sparse", "activity_round_count": 3,
                "conversation_sources": chat_sources, "private_session_ids": ["qq:FriendMessage:owner"], "events": [],
            })
            source = Source()
            self.assertTrue(await DiaryService(DiaryConfig.from_mapping({"owner_ids": ["owner"]}), storage, source).generate(date(2026, 7, 25), Provider(), force=True))
            rewritten = storage.load_metadata("2026-07-25")
            self.assertEqual(rewritten["entry_type"], "low_activity")
            self.assertEqual(rewritten["conversation_sources"], chat_sources)
            self.assertEqual(set(rewritten["historical_memory_candidate_ids"]), {"historical-1", "historical-2", "historical-3"})
            self.assertEqual(source.before_calls, 1)

    async def test_rewrite_preserves_normal_contract_without_daily_activity(self):
        class Source:
            def read_day(self, _day, limit=80):
                return [SourceMemory(str(index), datetime(2026, 7, 25, index + 1, tzinfo=timezone.utc), f"事实{index}") for index in range(5)]

            def read_range(self, *_args, **_kwargs):
                raise AssertionError("normal rewrite must not enter adaptive context collection")

            def read_before(self, *_args, **_kwargs):
                raise AssertionError("normal rewrite must not select historical memories")

        class Provider:
            def __init__(self): self.prompts = []

            async def text_chat(self, prompt, **_kwargs):
                self.prompts.append(prompt)
                return type("Response", (), {"completion_text": json.dumps({"markdown": "# normal\n", "title": "normal", "events": []})})()

        with tempfile.TemporaryDirectory() as temporary:
            storage = DiaryStorage(Path(temporary))
            storage.save_daily_activity("2026-07-25", {"round_count": 8, "private_session_ids": ["qq:FriendMessage:owner"]})
            provider = Provider()
            service = DiaryService(DiaryConfig.from_mapping({"owner_ids": ["owner"]}), storage, Source())
            self.assertTrue(await service.generate(date(2026, 7, 25), provider))
            self.assertFalse(storage.daily_activity_path("2026-07-25").exists())

            self.assertTrue(await service.generate(date(2026, 7, 25), provider, force=True))
            rewritten = storage.load_metadata("2026-07-25")
            self.assertEqual(rewritten["entry_type"], "normal")
            self.assertEqual(rewritten["activity_round_count"], 8)
            self.assertEqual(rewritten["prompt_version"], NORMAL_PROMPT_VERSION)
            self.assertIn('"entry_type": "normal"', provider.prompts[-1])

    async def test_adaptive_contract_rejects_missing_required_context_usage(self):
        class Source:
            def __init__(self, day): self.day = day
            def read_day(self, _day, limit=80): return self.day
            def read_range(self, *_args, **_kwargs):
                return [SourceMemory("recent", datetime(2026, 7, 23, 9, tzinfo=timezone.utc), "近期事实", session_id="qq:FriendMessage:owner")]
            def read_before(self, *_args, **_kwargs): return []

        class Provider:
            async def text_chat(self, **_kwargs):
                return type("Response", (), {"completion_text": json.dumps({
                    "markdown": "# 未使用上下文\n", "title": "invalid", "events": [],
                    "used_recent_memory_ids": [], "used_historical_memory_ids": [],
                })})()

        cases = {
            "sparse": {
                "round_count": 8,
                "day": [SourceMemory("today", datetime(2026, 7, 25, 9, tzinfo=timezone.utc), "当天事实")],
            },
            "low_activity": {"round_count": 1, "day": []},
        }
        for expected_mode, case in cases.items():
            with self.subTest(mode=expected_mode), tempfile.TemporaryDirectory() as temporary:
                storage = DiaryStorage(Path(temporary))
                storage.save_daily_activity("2026-07-25", {
                    "round_count": case["round_count"], "private_session_ids": ["qq:FriendMessage:owner"],
                })
                service = DiaryService(DiaryConfig.from_mapping({"owner_ids": ["owner"]}), storage, Source(case["day"]))
                self.assertIsNone(await service.generate(date(2026, 7, 25), Provider()))
                self.assertFalse(storage.diary_path("2026-07-25").exists())
                state = storage.load_generation_state()
                self.assertEqual(state.stage, "failed")
                self.assertIn("did not use required", state.last_error)

    async def test_adaptive_contract_failure_is_retried_before_writing(self):
        class Source:
            def read_day(self, _day, limit=80):
                return [SourceMemory("today", datetime(2026, 7, 25, 9, tzinfo=timezone.utc), "当天事实")]
            def read_range(self, *_args, **_kwargs):
                return [SourceMemory("recent", datetime(2026, 7, 23, 9, tzinfo=timezone.utc), "前天的事", session_id="qq:FriendMessage:owner")]
            def read_before(self, *_args, **_kwargs): return []

        class Provider:
            def __init__(self): self.calls = 0
            async def text_chat(self, **_kwargs):
                self.calls += 1
                used = [] if self.calls == 1 else ["recent"]
                return type("Response", (), {"completion_text": json.dumps({
                    "markdown": "# sparse\n", "events": [], "used_recent_memory_ids": used,
                })})()

        with tempfile.TemporaryDirectory() as temporary:
            storage = DiaryStorage(Path(temporary))
            storage.save_daily_activity("2026-07-25", {
                "round_count": 8, "private_session_ids": ["qq:FriendMessage:owner"],
            })
            provider = Provider()
            service = DiaryService(DiaryConfig.from_mapping({"provider_retry_count": 1}), storage, Source())
            self.assertTrue(await service.generate(date(2026, 7, 25), provider))
            self.assertEqual(provider.calls, 2)
            self.assertEqual(storage.load_metadata("2026-07-25")["recent_memory_used_ids"], ["recent"])

    async def test_empty_provider_events_still_preserve_all_same_day_source_evidence(self):
        class Source:
            def read_day(self, _day, limit=80):
                return [
                    SourceMemory("today-1", datetime(2026, 7, 25, 9, tzinfo=timezone.utc), "完成第一件事", topics=("工作",)),
                    SourceMemory("today-2", datetime(2026, 7, 25, 18, tzinfo=timezone.utc), "完成第二件事", topics=("生活",)),
                ]

        class Provider:
            async def text_chat(self, **_kwargs):
                return type("Response", (), {"completion_text": json.dumps({"markdown": "# d\n", "title": "d", "events": []})})()

        with tempfile.TemporaryDirectory() as temporary:
            storage = DiaryStorage(Path(temporary))
            self.assertTrue(await DiaryService(DiaryConfig.from_mapping({"owner_ids": ["owner"]}), storage, Source()).generate(date(2026, 7, 25), Provider()))
            metadata = storage.load_metadata("2026-07-25")
            self.assertEqual(set(metadata["memory_ids"]), {"today-1", "today-2"})
            self.assertEqual({memory_id for event in metadata["events"] for memory_id in event["memory_ids"]}, {"today-1", "today-2"})
            self.assertTrue(all(event["facts"] for event in metadata["events"]))

    async def test_partial_provider_evidence_is_completed_without_duplicate_event(self):
        class Source:
            def read_day(self, _day, limit=80):
                return [
                    SourceMemory("today-1", datetime(2026, 7, 25, 9, tzinfo=timezone.utc), "同一项目的第一步", topics=("项目",)),
                    SourceMemory("today-2", datetime(2026, 7, 25, 10, tzinfo=timezone.utc), "同一项目的第二步", topics=("项目",)),
                ]

        class Provider:
            async def text_chat(self, **_kwargs):
                return type("Response", (), {"completion_text": json.dumps({
                    "markdown": "# d\n", "title": "d",
                    "events": [{"summary": "项目进展", "memory_ids": ["today-1"], "facts": ["同一项目的第一步"]}],
                })})()

        with tempfile.TemporaryDirectory() as temporary:
            storage = DiaryStorage(Path(temporary))
            self.assertTrue(await DiaryService(DiaryConfig.from_mapping({"owner_ids": ["owner"]}), storage, Source()).generate(date(2026, 7, 25), Provider()))
            metadata = storage.load_metadata("2026-07-25")
            self.assertEqual(len(metadata["events"]), 1)
            self.assertEqual(metadata["events"][0]["memory_ids"], ["today-1", "today-2"])
            self.assertEqual(set(metadata["memory_ids"]), {"today-1", "today-2"})

    async def test_recent_and_historical_sources_never_become_same_day_events(self):
        class Source:
            def read_day(self, _day, limit=80):
                return [SourceMemory("today", datetime(2026, 7, 25, 9, tzinfo=timezone.utc), "今天的事实", session_id="qq:FriendMessage:owner")]
            def read_range(self, *_args, **_kwargs):
                return [SourceMemory("recent", datetime(2026, 7, 23, 9, tzinfo=timezone.utc), "近期回忆", session_id="qq:FriendMessage:owner")]
            def read_before(self, *_args, **_kwargs):
                return [SourceMemory("historical", datetime(2026, 6, 1, 9, tzinfo=timezone.utc), "历史回忆", session_id="qq:FriendMessage:owner")]

        class Provider:
            async def text_chat(self, **_kwargs):
                return type("Response", (), {"completion_text": json.dumps({
                    "markdown": "# d\n", "title": "d",
                    "events": [{"summary": "非法混入", "memory_ids": ["recent", "historical"], "facts": ["回忆"]}],
                    "used_recent_memory_ids": ["recent", "fake"],
                    "used_historical_memory_ids": ["historical", "fake"],
                })})()

        with tempfile.TemporaryDirectory() as temporary:
            storage = DiaryStorage(Path(temporary))
            storage.save_daily_activity("2026-07-25", {"round_count": 1, "private_session_ids": ["qq:FriendMessage:owner"]})
            self.assertTrue(await DiaryService(DiaryConfig.from_mapping({"owner_ids": ["owner"]}), storage, Source()).generate(date(2026, 7, 25), Provider()))
            metadata = storage.load_metadata("2026-07-25")
            event_ids = {memory_id for event in metadata["events"] for memory_id in event["memory_ids"]}
            self.assertEqual(event_ids, {"today"})
            self.assertEqual(metadata["recent_memory_used_ids"], ["recent"])
            self.assertEqual(metadata["historical_memory_used_ids"], ["historical"])

    async def test_v111_metadata_without_recent_usage_field_remains_readable(self):
        with tempfile.TemporaryDirectory() as temporary:
            storage = DiaryStorage(Path(temporary))
            storage.metadata_path("2026-07-25").parent.mkdir(parents=True)
            storage.metadata_path("2026-07-25").write_text(json.dumps({
                "date": "2026-07-25", "title": "v1.1.1", "prompt_version": "v1.1",
                "events": [], "recent_context_sources": [{"memory_id": "recent"}],
            }), encoding="utf-8")
            metadata = storage.load_metadata("2026-07-25")
            self.assertEqual(metadata["title"], "v1.1.1")
            self.assertNotIn("recent_memory_used_ids", metadata)

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

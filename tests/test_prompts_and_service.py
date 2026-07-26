import importlib
import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from diary.config import DiaryConfig
from diary.models import ContinuityState, DiaryEvent, SourceMemory
from diary.storage import DiaryStorage


def load_module(name):
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        raise AssertionError(f"missing module: {name}") from exc


class PromptTests(unittest.TestCase):
    def test_all_modes_share_the_configured_main_prompt(self):
        prompts = load_module("diary.prompts")
        configured_prompt = "这是用户自己填写的唯一日记主提示词。"
        config = DiaryConfig.from_mapping({"diary_main_prompt": configured_prompt})
        persona = "你是 AstrBot 当前选择的人格；保持自己的姓名和说话方式。"
        normal_system, _ = prompts.build_messages("2026-07-25", [], ContinuityState(), config, [], persona)
        sparse_system, _ = prompts.build_adaptive_messages(
            "2026-07-25",
            "sparse",
            [],
            ContinuityState(),
            config,
            [],
            [{"memory_id": "recent-1", "occurred_at": "2026-07-23T10:00:00+08:00", "text": "近期记忆"}],
            [],
            persona,
        )
        low_system, _ = prompts.build_adaptive_messages(
            "2026-07-25", "low_activity", [], ContinuityState(), config, [], [], [{"memory_id": "historical-1"}], persona,
        )
        self.assertEqual(normal_system, sparse_system)
        self.assertEqual(sparse_system, low_system)
        self.assertIn(persona, normal_system)
        self.assertIn(configured_prompt, normal_system)
        self.assertNotIn("文字应当像一天结束时自然写下的私人记录", normal_system)
        self.assertNotIn("千早爱音", normal_system)
        self.assertNotIn("虾仁", normal_system)
        self.assertIn("mode_contract 是素材使用契约", normal_system)

    def test_blank_main_prompt_keeps_only_persona_and_safety_contract(self):
        prompts = load_module("diary.prompts")
        system, _ = prompts.build_messages(
            "2026-07-25", [], ContinuityState(),
            DiaryConfig.from_mapping({"diary_main_prompt": ""}),
            astrbot_persona_prompt="ASTRBOT PERSONA",
        )
        self.assertIn("ASTRBOT PERSONA", system)
        self.assertNotIn("用户配置的日记主提示词", system)
        self.assertIn("日记素材与输出契约", system)

    def test_markdown_is_bound_by_the_same_fact_and_date_rules(self):
        prompts = load_module("diary.prompts")
        system, _ = prompts.build_messages("2026-07-25", [], ContinuityState(), DiaryConfig())
        self.assertIn("同时约束 markdown 正文与 structured events", system)
        self.assertIn("不得据此断言某件事当前仍然成立", system)
        self.assertIn("正文仍须保留该日期", system)

    def test_sparse_contract_requires_recent_context_and_excludes_history(self):
        prompts = load_module("diary.prompts")
        _, material = prompts.build_adaptive_messages(
            "2026-07-25", "sparse", [], ContinuityState(), DiaryConfig(),
            [{"user_text": "当天私聊"}], [{"memory_id": "recent-1"}], [{"memory_id": "must-not-be-included"}],
        )
        payload = json.loads(material)
        contract = payload["mode_contract"]
        self.assertEqual(payload["prompt_version"], prompts.UNIFIED_PROMPT_VERSION)
        self.assertEqual(contract["mode"], "sparse")
        self.assertEqual(contract["required_usage"]["recent_context_sources"]["minimum_used"], 1)
        self.assertIn("recent_context_sources", contract["context_only_sources"])
        self.assertIn("recent_context_sources", contract["preserve_original_date_for"])
        self.assertIn("historical_memory_sources", contract["forbidden_sources"])
        self.assertNotIn("historical_memory_sources", payload)

    def test_low_activity_contract_allows_recent_and_frozen_history(self):
        prompts = load_module("diary.prompts")
        _, material = prompts.build_adaptive_messages(
            "2026-07-25",
            "low_activity",
            [],
            ContinuityState(),
            DiaryConfig(),
            [],
            [{"memory_id": "recent-1"}],
            [{"memory_id": "historical-1"}],
        )
        payload = json.loads(material)
        contract = payload["mode_contract"]
        self.assertEqual(contract["required_usage"]["recent_or_historical"]["minimum_used"], 1)
        self.assertEqual(
            contract["preserve_original_date_for"],
            ["recent_context_sources", "historical_memory_sources"],
        )
        self.assertEqual(payload["historical_memory_sources"], [{"memory_id": "historical-1"}])

    def test_normal_contract_only_packages_today_conversation_and_continuity(self):
        prompts = load_module("diary.prompts")
        _, material = prompts.build_messages(
            "2026-07-25", [], ContinuityState(), DiaryConfig(), [{"user_text": "当天私聊"}],
        )
        payload = json.loads(material)
        self.assertEqual(payload["entry_type"], "normal")
        self.assertEqual(payload["conversation_sources"], [{"user_text": "当天私聊"}])
        self.assertEqual(payload["mode_contract"]["today_fact_sources"], ["today_events"])
        self.assertIn("conversation_sources", payload["mode_contract"]["context_only_sources"])
        self.assertNotIn("recent_context_sources", payload)
        self.assertNotIn("historical_memory_sources", payload)

    def test_parser_removes_unknown_evidence_and_keeps_inference_separate(self):
        prompts = load_module("diary.prompts")
        raw = json.dumps({
            "markdown": "# Diary\n\n事实内容。",
            "title": "Diary",
            "events": [{"summary": "完成工作", "memory_ids": ["1", "unknown"], "facts": ["完成工作"], "inferences": ["可能感到轻松"]}],
            "topics": ["work"],
        })
        parsed = prompts.parse_diary_response(raw, "2026-07-25", {"1"})
        self.assertEqual(parsed.metadata.events[0].memory_ids, ["1"])
        self.assertEqual(parsed.metadata.events[0].inferences, ["可能感到轻松"])

    def test_parser_rejects_fabricated_historical_memory_ids(self):
        prompts = load_module("diary.prompts")
        raw = json.dumps({"markdown": "# Diary\n\n回忆。", "title": "Diary", "events": [], "used_historical_memory_ids": ["candidate", "fabricated"]})
        parsed = prompts.parse_diary_response(raw, "2026-07-25", set(), {"candidate"})
        self.assertEqual(parsed.used_historical_memory_ids, ["candidate"])

    def test_parser_accepts_only_used_recent_candidates_and_never_invents_usage(self):
        prompts = load_module("diary.prompts")
        raw = json.dumps({
            "markdown": "# Diary\n\n想起了前天的事。",
            "title": "Diary",
            "events": [],
            "used_recent_memory_ids": ["recent-1", "fabricated", "recent-1"],
        })
        parsed = prompts.parse_diary_response(
            raw,
            "2026-07-25",
            set(),
            set(),
            {"recent-1"},
            prompts.ADAPTIVE_PROMPT_VERSION,
        )
        self.assertEqual(parsed.used_recent_memory_ids, ["recent-1"])
        self.assertEqual(parsed.metadata.prompt_version, prompts.ADAPTIVE_PROMPT_VERSION)

        unused = prompts.parse_diary_response(
            json.dumps({"markdown": "# Diary\n\n没有使用近期记忆。", "title": "Diary", "events": []}),
            "2026-07-25",
            set(),
            set(),
            {"recent-1"},
            prompts.ADAPTIVE_PROMPT_VERSION,
        )
        self.assertEqual(unused.used_recent_memory_ids, [])


class FakeSource:
    def read_day(self, _day, limit=80):
        return [SourceMemory("1", datetime(2026, 7, 25, 9), "完成权限修复", topics=("日记",))]


class FlakyProvider:
    def __init__(self):
        self.calls = 0

    async def text_chat(self, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("offline")
        return type("Response", (), {"completion_text": json.dumps({"markdown": "# 7月25日\n\n完成了权限修复。", "title": "7月25日", "events": [{"summary": "完成权限修复", "memory_ids": ["1"], "facts": ["完成权限修复"]}]})})()


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_generation_retries_malformed_json_and_uses_astrbot_persona(self):
        service_module = load_module("diary.service")

        class Provider:
            def __init__(self): self.calls = 0; self.systems = []
            async def text_chat(self, **kwargs):
                self.calls += 1; self.systems.append(kwargs["system_prompt"])
                text = "not json" if self.calls == 1 else json.dumps({"markdown": "# ok\n", "events": []})
                return type("Response", (), {"completion_text": text})()

        async def persona_resolver(sessions):
            self.assertEqual(sessions, ["qq:FriendMessage:1"])
            return "ASTRBOT PERSONA"

        with tempfile.TemporaryDirectory() as temp_dir:
            storage = DiaryStorage(Path(temp_dir))
            provider = Provider()
            service = service_module.DiaryService(
                DiaryConfig.from_mapping({"provider_retry_count": 1}), storage, FakeSource(),
                persona_resolver=persona_resolver,
            )
            result = await service.generate(date(2026, 7, 25), provider, persona_session_id="qq:FriendMessage:1")
            self.assertTrue(result)
            self.assertEqual(provider.calls, 2)
            self.assertTrue(all("ASTRBOT PERSONA" in value for value in provider.systems))

    async def test_existing_daily_returns_an_unchanged_result(self):
        service_module = load_module("diary.service")
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = DiaryStorage(Path(temp_dir))
            storage.diary_path("2026-07-25").parent.mkdir(parents=True, exist_ok=True)
            storage.diary_path("2026-07-25").write_text("# existing\n", encoding="utf-8")
            storage.metadata_path("2026-07-25").parent.mkdir(parents=True, exist_ok=True)
            storage.metadata_path("2026-07-25").write_text('{"date":"2026-07-25"}', encoding="utf-8")
            result = await service_module.DiaryService(DiaryConfig(), storage, FakeSource()).generate(date(2026, 7, 25), object())
            self.assertTrue(result)
            self.assertFalse(service_module.diary_changed(result))

    async def test_generation_retries_persists_pair_and_clears_pending_state(self):
        service_module = load_module("diary.service")
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = DiaryStorage(Path(temp_dir))
            provider = FlakyProvider()
            service = service_module.DiaryService(
                DiaryConfig.from_mapping({"owner_ids": ["1"], "provider_retry_count": 1}),
                storage,
                FakeSource(),
            )
            result = await service.generate(date(2026, 7, 25), provider)
            self.assertTrue(result)
            self.assertEqual(provider.calls, 2)
            self.assertTrue(storage.has_diary("2026-07-25"))
            self.assertEqual(storage.load_generation_state().pending_date, "")
            self.assertEqual(storage.load_generation_state().retry_count, 1)
            self.assertTrue(storage.load_generation_state().last_success_at)

    async def test_failed_generation_records_recovery_information(self):
        service_module = load_module("diary.service")

        class BrokenProvider:
            async def text_chat(self, **_kwargs):
                raise RuntimeError("unavailable")

        with tempfile.TemporaryDirectory() as temp_dir:
            storage = DiaryStorage(Path(temp_dir))
            service = service_module.DiaryService(DiaryConfig.from_mapping({"owner_ids": ["1"]}), storage, FakeSource())
            self.assertIsNone(await service.generate(date(2026, 7, 25), BrokenProvider()))
            state = storage.load_generation_state()
            self.assertEqual((state.pending_date, state.stage), ("2026-07-25", "failed"))
            self.assertIn("unavailable", state.last_error)

    async def test_provider_timeout_records_failure_without_diary_files(self):
        service_module = load_module("diary.service")

        class HangingProvider:
            async def text_chat(self, **_kwargs):
                await __import__("asyncio").sleep(1)

        with tempfile.TemporaryDirectory() as temp_dir:
            storage = DiaryStorage(Path(temp_dir))
            previous = service_module.PROVIDER_TIMEOUT_SECONDS
            service_module.PROVIDER_TIMEOUT_SECONDS = 0.01
            try:
                service = service_module.DiaryService(DiaryConfig.from_mapping({"owner_ids": ["1"], "provider_retry_count": 0}), storage, FakeSource())
                self.assertIsNone(await service.generate(date(2026, 7, 25), HangingProvider()))
            finally:
                service_module.PROVIDER_TIMEOUT_SECONDS = previous
            self.assertFalse(storage.has_any_diary("2026-07-25"))
            self.assertEqual(storage.load_generation_state().stage, "failed")


if __name__ == "__main__":
    unittest.main()

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
    def test_sparse_prompt_requires_using_available_recent_context(self):
        prompts = load_module("diary.prompts")
        system, material = prompts.build_adaptive_messages(
            "2026-07-25",
            "sparse",
            [],
            ContinuityState(),
            DiaryConfig(),
            [],
            [{"memory_id": "recent-1", "occurred_at": "2026-07-23T10:00:00+08:00", "text": "近期记忆"}],
            [],
        )
        self.assertIn("必须主动实际使用至少 1 条近期记忆", system)
        self.assertIn("不要求与当天事件强行拼接", system)
        self.assertIn("不要只把当天少量 memory 扩写成普通事件日报", system)
        self.assertIn("绝不能改写成今天发生", system)
        self.assertIn("used_recent_memory_ids", system)
        self.assertEqual(json.loads(material)["prompt_version"], prompts.ADAPTIVE_PROMPT_VERSION)

    def test_low_activity_prompt_encourages_available_context_without_forcing_all_candidates(self):
        prompts = load_module("diary.prompts")
        system, _ = prompts.build_adaptive_messages(
            "2026-07-25",
            "low_activity",
            [],
            ContinuityState(),
            DiaryConfig(),
            [],
            [{"memory_id": "recent-1"}],
            [{"memory_id": "historical-1"}],
        )
        self.assertIn("必须主动从 recent_context_sources / historical_memory_sources 中选择至少 1 条实际展开", system)
        self.assertIn("回忆、自言自语、感慨或自由联想", system)
        self.assertIn("不要求使用全部候选", system)
        self.assertIn("必须使用不确定表达", system)

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

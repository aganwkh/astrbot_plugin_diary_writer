from datetime import datetime
import importlib
import unittest

from diary.models import SourceMemory


def load_events():
    try:
        return importlib.import_module("diary.events")
    except ModuleNotFoundError as exc:
        raise AssertionError("missing events module") from exc


class EventExtractionTests(unittest.TestCase):
    def test_duplicate_and_related_memories_form_one_evidenced_event(self):
        events = load_events()
        when = datetime(2026, 7, 25, 9)
        records = [
            SourceMemory("1", when, "完成了日记插件的权限修复", topics=("日记", "插件")),
            SourceMemory("2", when, "完成了日记插件的权限修复", topics=("日记", "插件")),
            SourceMemory("3", datetime(2026, 7, 25, 10), "开始测试日记插件的权限修复", topics=("日记", "插件")),
        ]
        result = events.cluster_memories(records)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].memory_ids, ["1", "2", "3"])
        self.assertIn("权限修复", result[0].summary)

    def test_unrelated_memories_are_not_merged(self):
        events = load_events()
        records = [
            SourceMemory("1", datetime(2026, 7, 25, 9), "修复插件权限", topics=("插件",)),
            SourceMemory("2", datetime(2026, 7, 25, 20), "晚饭吃了面", topics=("生活",)),
        ]
        self.assertEqual(len(events.cluster_memories(records)), 2)


if __name__ == "__main__":
    unittest.main()

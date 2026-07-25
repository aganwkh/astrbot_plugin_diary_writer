from datetime import date, datetime, timezone
import importlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path


def load_source():
    try:
        return importlib.import_module("diary.memory_source")
    except ModuleNotFoundError as exc:
        raise AssertionError("missing memory source module") from exc


class MemorySourceTests(unittest.TestCase):
    def test_reads_only_requested_day_from_legacy_livingmemory_database(self):
        source_module = load_source()
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "livingmemory.db"
            connection = sqlite3.connect(database)
            try:
                connection.execute("CREATE TABLE documents (id TEXT, text TEXT, metadata TEXT)")
                connection.execute(
                    "INSERT INTO documents VALUES (?, ?, ?)",
                    ("today", "today text", json.dumps({"create_time": 1784937600, "topics": ["work"]})),
                )
                connection.execute(
                    "INSERT INTO documents VALUES (?, ?, ?)",
                    ("other", "other text", json.dumps({"create_time": 1784851200})),
                )
                connection.commit()
            finally:
                connection.close()
            records = source_module.SQLiteLivingMemorySource(database).read_day(date(2026, 7, 25))
            self.assertEqual([record.memory_id for record in records], ["today"])

    def test_schema_mismatch_fails_without_writing_database(self):
        source_module = load_source()
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "livingmemory.db"
            connection = sqlite3.connect(database)
            try:
                connection.execute("CREATE TABLE unrelated (id TEXT)")
                connection.commit()
            finally:
                connection.close()
            with self.assertRaises(source_module.MemorySourceError):
                source_module.SQLiteLivingMemorySource(database).read_day(date(2026, 7, 25))

    def test_recent_and_historical_reads_are_private_session_only_and_never_cross_target_date(self):
        source_module = load_source()
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "livingmemory.db"
            connection = sqlite3.connect(database)
            try:
                connection.execute("CREATE TABLE documents (id TEXT, text TEXT, metadata TEXT)")
                rows = [
                    ("recent", "recent text", datetime(2026, 7, 24, tzinfo=timezone.utc), "qq:FriendMessage:owner"),
                    ("old", "old text", datetime(2026, 6, 1, tzinfo=timezone.utc), "qq:FriendMessage:owner"),
                    ("group", "group text", datetime(2026, 6, 1, tzinfo=timezone.utc), "qq:GroupMessage:owner"),
                    ("future", "future text", datetime(2026, 7, 26, tzinfo=timezone.utc), "qq:FriendMessage:owner"),
                ]
                for memory_id, text, occurred_at, session_id in rows:
                    connection.execute("INSERT INTO documents VALUES (?, ?, ?)", (memory_id, text, json.dumps({"create_time": occurred_at.timestamp(), "session_id": session_id})))
                connection.commit()
            finally:
                connection.close()
            source = source_module.SQLiteLivingMemorySource(database)
            sessions = {"qq:FriendMessage:owner"}
            self.assertEqual([item.memory_id for item in source.read_range(date(2026, 7, 22), date(2026, 7, 24), sessions)], ["recent"])
            self.assertEqual([item.memory_id for item in source.read_before(date(2026, 7, 25), sessions)], ["old", "recent"])


if __name__ == "__main__":
    unittest.main()

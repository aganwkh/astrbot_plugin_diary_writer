from datetime import date
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


if __name__ == "__main__":
    unittest.main()

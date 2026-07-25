from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Protocol

from .models import SourceMemory


class MemorySourceError(RuntimeError):
    pass


class MemorySource(Protocol):
    def read_day(self, diary_date: date, limit: int = 80) -> list[SourceMemory]:
        """Return source memories without changing the upstream store."""

    def read_range(self, start: date, end: date, session_ids: set[str], limit: int = 80) -> list[SourceMemory]:
        """Return only explicitly known private-session memories in a date range."""

    def read_before(self, before: date, session_ids: set[str], limit: int = 500) -> list[SourceMemory]:
        """Return only explicitly known private-session memories before a day."""


def _timestamp(metadata: dict) -> float | None:
    for key in ("create_time", "timestamp", "created_at", "time"):
        try:
            value = float(metadata.get(key))
            if value > 0:
                return value
        except (TypeError, ValueError):
            continue
    return None


def _text_list(value) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


class SQLiteLivingMemorySource:
    """Compatibility adapter for LivingMemory's legacy documents store only."""

    def __init__(self, database_path: Path):
        self.database_path = database_path

    def read_day(self, diary_date: date, limit: int = 80) -> list[SourceMemory]:
        return self._read(lambda item: item.occurred_at.date() == diary_date, limit)

    def read_range(self, start: date, end: date, session_ids: set[str], limit: int = 80) -> list[SourceMemory]:
        if not session_ids:
            return []
        return self._read(lambda item: start <= item.occurred_at.date() <= end and item.session_id in session_ids, limit)

    def read_before(self, before: date, session_ids: set[str], limit: int = 500) -> list[SourceMemory]:
        if not session_ids:
            return []
        return self._read(lambda item: item.occurred_at.date() < before and item.session_id in session_ids, limit)

    def _read(self, include, limit: int) -> list[SourceMemory]:
        if not self.database_path.is_file():
            raise MemorySourceError(f"LivingMemory database not found: {self.database_path}")
        uri = f"{self.database_path.resolve().as_uri()}?mode=ro"
        connection = None
        try:
            connection = sqlite3.connect(uri, uri=True)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(documents)")}
            required = {"id", "text", "metadata"}
            if not required.issubset(columns):
                raise MemorySourceError("LivingMemory documents schema is unavailable or incompatible")
            rows = connection.execute("SELECT id, text, metadata FROM documents").fetchall()
        except MemorySourceError:
            raise
        except sqlite3.Error as exc:
            raise MemorySourceError(f"could not read LivingMemory database: {exc}") from exc
        finally:
            if connection is not None:
                connection.close()

        records: list[SourceMemory] = []
        for memory_id, text, raw_metadata in rows:
            try:
                metadata = json.loads(raw_metadata or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            timestamp = _timestamp(metadata)
            if timestamp is None or not str(text or "").strip():
                continue
            occurred_at = datetime.fromtimestamp(timestamp)
            try:
                importance = float(metadata.get("importance") or 0)
            except (TypeError, ValueError):
                importance = 0.0
            record = SourceMemory(
                    memory_id=str(memory_id),
                    occurred_at=occurred_at,
                    text=str(text).strip(),
                    importance=importance,
                    session_id=str(metadata.get("session_id") or ""),
                    topics=_text_list(metadata.get("topics")),
                    key_facts=_text_list(metadata.get("key_facts")),
                )
            if include(record):
                records.append(record)
        records.sort(key=lambda item: (item.occurred_at, -item.importance, item.memory_id))
        return records[:max(0, limit)]

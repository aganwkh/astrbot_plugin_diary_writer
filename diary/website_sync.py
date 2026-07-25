from __future__ import annotations

import json
from pathlib import Path

from .models import DiaryMetadata
from .storage import atomic_write_json, atomic_write_text


class WebsiteSync:
    def __init__(self, target: Path):
        self.target = target

    def sync(self, date: str, markdown: str, metadata: DiaryMetadata) -> None:
        self.target.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.target / f"{date}.md", markdown)
        index_path = self.target.parent / "diaries.json"
        try:
            entries = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else []
        except (OSError, json.JSONDecodeError):
            entries = []
        entries = [entry for entry in entries if entry.get("date") != date]
        entries.append({"date": date, "title": metadata.title, "mood": metadata.mood, "tags": metadata.tags, "file": f"{date}.md"})
        entries.sort(key=lambda entry: entry["date"], reverse=True)
        atomic_write_json(index_path, entries)

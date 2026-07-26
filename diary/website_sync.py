from __future__ import annotations

import json
import re
from pathlib import Path

from .models import DiaryMetadata
from .storage import atomic_write_json, atomic_write_text


class WebsiteSync:
    def __init__(self, target: Path):
        self.target = target

    def sync(self, date: str, markdown: str, metadata: DiaryMetadata) -> None:
        self.target.mkdir(parents=True, exist_ok=True)
        markdown_path = self.target / f"{date}.md"
        atomic_write_text(markdown_path, markdown)
        markdown_path.chmod(0o644)
        index_path = self.target.parent / "diaries.json"
        if index_path.exists():
            try:
                entries = json.loads(index_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise OSError("existing website diary index is unreadable; refusing to replace it") from exc
            if not isinstance(entries, list):
                raise ValueError("existing website diary index must be a list")
        else:
            entries = []
        entries = [entry for entry in entries if isinstance(entry, dict)]
        dotted_dates = any(re.fullmatch(r"\d{4}\.\d{2}\.\d{2}", str(entry.get("date") or "")) for entry in entries)
        entries = [entry for entry in entries if self._date_key(entry.get("date")) != date]
        display_date = date.replace("-", ".") if dotted_dates else date
        entries.append({
            "date": display_date,
            "title": metadata.title,
            "mood": metadata.mood,
            "tags": metadata.tags,
            "file": f"{self.target.name}/{date}.md",
        })
        entries.sort(key=lambda entry: self._date_key(entry.get("date")), reverse=True)
        atomic_write_json(index_path, entries)
        index_path.chmod(0o644)

    @staticmethod
    def _date_key(value: object) -> str:
        return str(value or "").replace(".", "-").replace("/", "-")

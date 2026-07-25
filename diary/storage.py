from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import ContinuityState, DiaryMetadata, GenerationState, as_jsonable


def _write_temp(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, text=True)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        return temp_path
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def atomic_write_text(path: Path, content: str) -> None:
    temp_path = _write_temp(path, content)
    try:
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(as_jsonable(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _new_backup_dir(root: Path, *parts: str) -> Path:
    parent = root.joinpath(*parts)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    for counter in range(1_000):
        path = parent / (stamp if counter == 0 else f"{stamp}-{counter}")
        try:
            path.mkdir(parents=True)
            return path
        except FileExistsError:
            continue
    raise OSError("could not allocate backup directory")


class DiaryStorage:
    def __init__(self, root: Path):
        self.root = root
        self.diary_root = root / "diaries"
        self.metadata_root = root / "metadata"
        self.backup_root = root / "backups"
        self.review_root = root / "reviews"
        self.review_metadata_root = root / "review_metadata"
        self.review_backup_root = root / "review_backups"
        self.review_state_path = root / "review_generation_state.json"
        self.state_path = root / "generation_state.json"
        self.continuity_path = root / "continuity.json"
        self.activity_path = root / "activity.json"
        self.reminder_state_path = root / "reminder_state.json"

    def diary_path(self, date: str) -> Path:
        return self.diary_root / f"{date}.md"

    def metadata_path(self, date: str) -> Path:
        return self.metadata_root / f"{date}.json"

    def review_path(self, kind: str, period: str) -> Path:
        return self.review_root / kind / f"{period}.md"

    def review_metadata_path(self, kind: str, period: str) -> Path:
        return self.review_metadata_root / kind / f"{period}.json"

    def has_diary(self, date: str) -> bool:
        return self.diary_path(date).is_file() and self.metadata_path(date).is_file()

    def has_any_diary(self, date: str) -> bool:
        return self.diary_path(date).is_file()

    def has_review(self, kind: str, period: str) -> bool:
        return self.review_path(kind, period).is_file() and self.review_metadata_path(kind, period).is_file()

    def write_diary(self, date: str, markdown: str, metadata: DiaryMetadata, backup_existing: bool = False) -> None:
        markdown_path = self.diary_path(date)
        metadata_path = self.metadata_path(date)
        if backup_existing and (markdown_path.exists() or metadata_path.exists()):
            self.backup_diary(date)

        markdown_temp = _write_temp(markdown_path, markdown)
        metadata_temp = _write_temp(
            metadata_path,
            json.dumps(as_jsonable(metadata), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        old_markdown = markdown_path.read_text(encoding="utf-8") if markdown_path.exists() else None
        old_metadata = metadata_path.read_text(encoding="utf-8") if metadata_path.exists() else None
        try:
            os.replace(markdown_temp, markdown_path)
            os.replace(metadata_temp, metadata_path)
        except Exception:
            if old_markdown is not None:
                atomic_write_text(markdown_path, old_markdown)
            elif markdown_path.exists():
                markdown_path.unlink()
            if old_metadata is not None:
                atomic_write_text(metadata_path, old_metadata)
            elif metadata_path.exists():
                metadata_path.unlink()
            raise
        finally:
            markdown_temp.unlink(missing_ok=True)
            metadata_temp.unlink(missing_ok=True)

    def backup_diary(self, date: str) -> Path:
        backup_dir = _new_backup_dir(self.backup_root, date)
        markdown_path = self.diary_path(date)
        metadata_path = self.metadata_path(date)
        if markdown_path.exists():
            atomic_write_text(backup_dir / markdown_path.name, markdown_path.read_text(encoding="utf-8"))
        if metadata_path.exists():
            atomic_write_text(backup_dir / metadata_path.name, metadata_path.read_text(encoding="utf-8"))
        return backup_dir

    def write_review(self, kind: str, period: str, markdown: str, metadata: dict[str, Any], backup_existing: bool = False) -> None:
        markdown_path = self.review_path(kind, period)
        metadata_path = self.review_metadata_path(kind, period)
        if backup_existing and (markdown_path.exists() or metadata_path.exists()):
            self.backup_review(kind, period)
        self._write_pair(markdown_path, metadata_path, markdown, metadata)

    def backup_review(self, kind: str, period: str) -> Path:
        backup_dir = _new_backup_dir(self.review_backup_root, kind, period)
        markdown_path = self.review_path(kind, period)
        metadata_path = self.review_metadata_path(kind, period)
        if markdown_path.exists():
            atomic_write_text(backup_dir / markdown_path.name, markdown_path.read_text(encoding="utf-8"))
        if metadata_path.exists():
            atomic_write_text(backup_dir / metadata_path.name, metadata_path.read_text(encoding="utf-8"))
        return backup_dir

    def load_review_metadata(self, kind: str, period: str) -> dict[str, Any] | None:
        return self._load_json(self.review_metadata_path(kind, period))

    def iter_review_metadata(self):
        if not self.review_metadata_root.exists():
            return
        for kind_path in self.review_metadata_root.iterdir():
            if not kind_path.is_dir():
                continue
            for path in sorted(kind_path.glob("*.json")):
                data = self._load_json(path)
                if data is not None:
                    yield kind_path.name, path.stem, data

    def load_metadata(self, date: str) -> dict[str, Any] | None:
        return self._load_json(self.metadata_path(date))

    def iter_daily_metadata(self):
        if not self.metadata_root.exists():
            return
        for path in sorted(self.metadata_root.glob("*.json")):
            data = self._load_json(path)
            if data is not None:
                yield data

    def save_generation_state(self, state: GenerationState) -> None:
        atomic_write_json(self.state_path, state)

    def load_generation_state(self) -> GenerationState:
        return self._load_dataclass(self.state_path, GenerationState)

    def save_continuity(self, state: ContinuityState) -> None:
        atomic_write_json(self.continuity_path, state)

    def load_continuity(self) -> ContinuityState:
        return self._load_dataclass(self.continuity_path, ContinuityState)

    def save_activity(self, timestamp: str) -> None:
        atomic_write_json(self.activity_path, {"last_active_at": timestamp})

    def load_activity(self) -> str:
        try:
            data = json.loads(self.activity_path.read_text(encoding="utf-8"))
            return str(data.get("last_active_at") or "")
        except (OSError, AttributeError, json.JSONDecodeError):
            return ""

    def save_reminder_state(self, state: dict[str, Any]) -> None:
        atomic_write_json(self.reminder_state_path, state)

    def load_reminder_state(self) -> dict[str, Any]:
        return self._load_json(self.reminder_state_path) or {}

    @staticmethod
    def _load_dataclass(path: Path, model_type):
        if not path.exists():
            return model_type()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return model_type(**data) if isinstance(data, dict) else model_type()
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return model_type()

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any] | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (OSError, json.JSONDecodeError):
            return None

    @staticmethod
    def _write_pair(markdown_path: Path, metadata_path: Path, markdown: str, metadata: Any) -> None:
        markdown_temp = _write_temp(markdown_path, markdown)
        metadata_temp = _write_temp(metadata_path, json.dumps(as_jsonable(metadata), ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        old_markdown = markdown_path.read_text(encoding="utf-8") if markdown_path.exists() else None
        old_metadata = metadata_path.read_text(encoding="utf-8") if metadata_path.exists() else None
        try:
            os.replace(markdown_temp, markdown_path)
            os.replace(metadata_temp, metadata_path)
        except Exception:
            if old_markdown is not None:
                atomic_write_text(markdown_path, old_markdown)
            elif markdown_path.exists():
                markdown_path.unlink()
            if old_metadata is not None:
                atomic_write_text(metadata_path, old_metadata)
            elif metadata_path.exists():
                metadata_path.unlink()
            raise
        finally:
            markdown_temp.unlink(missing_ok=True)
            metadata_temp.unlink(missing_ok=True)

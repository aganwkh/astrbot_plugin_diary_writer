from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .models import ContinuityState, DiaryMetadata, GenerationState, as_jsonable, normalize_daily_metadata


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
        self.revision_root = root / "revisions"
        self.correction_root = root / "corrections"
        self.revision_state_root = root / "revision_state"
        self.reflection_root = root / "reflections"
        self.reflection_metadata_root = root / "reflection_metadata"
        self.reflection_backup_root = root / "reflection_backups"
        self.reflection_state_path = root / "reflection_generation_state.json"

    def diary_path(self, date: str) -> Path:
        return self.diary_root / f"{date}.md"

    def metadata_path(self, date: str) -> Path:
        return self.metadata_root / f"{date}.json"

    def review_path(self, kind: str, period: str) -> Path:
        return self.review_root / kind / f"{period}.md"

    def review_metadata_path(self, kind: str, period: str) -> Path:
        return self.review_metadata_root / kind / f"{period}.json"

    def reflection_path(self, kind: str, period: str) -> Path:
        return self.reflection_root / kind / f"{period}.md"

    def reflection_metadata_path(self, kind: str, period: str) -> Path:
        return self.reflection_metadata_root / kind / f"{period}.json"

    def has_diary(self, date: str) -> bool:
        return self.diary_path(date).is_file() and self.metadata_path(date).is_file()

    def has_any_diary(self, date: str) -> bool:
        return self.diary_path(date).is_file()

    def has_review(self, kind: str, period: str) -> bool:
        return self.review_path(kind, period).is_file() and self.review_metadata_path(kind, period).is_file()

    def has_reflection(self, kind: str, period: str) -> bool:
        return self.reflection_path(kind, period).is_file() and self.reflection_metadata_path(kind, period).is_file()

    def write_diary(self, date: str, markdown: str, metadata: DiaryMetadata, backup_existing: bool = False) -> None:
        self.write_diary_data(date, markdown, as_jsonable(metadata), backup_existing)

    def write_diary_data(self, date: str, markdown: str, metadata: dict[str, Any], backup_existing: bool = False, *, normalize: bool = True) -> None:
        markdown_path = self.diary_path(date)
        metadata_path = self.metadata_path(date)
        if backup_existing and (markdown_path.exists() or metadata_path.exists()):
            self.backup_diary(date)

        markdown_temp = _write_temp(markdown_path, markdown)
        metadata_temp = _write_temp(
            metadata_path,
            json.dumps(normalize_daily_metadata(metadata) if normalize else as_jsonable(metadata), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
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

    def write_reflection(self, kind: str, period: str, markdown: str, metadata: dict[str, Any], backup_existing: bool = False) -> None:
        markdown_path = self.reflection_path(kind, period)
        metadata_path = self.reflection_metadata_path(kind, period)
        if backup_existing and (markdown_path.exists() or metadata_path.exists()):
            backup_dir = _new_backup_dir(self.reflection_backup_root, kind, period)
            if markdown_path.exists():
                atomic_write_text(backup_dir / markdown_path.name, markdown_path.read_text(encoding="utf-8"))
            if metadata_path.exists():
                atomic_write_text(backup_dir / metadata_path.name, metadata_path.read_text(encoding="utf-8"))
        self._write_pair(markdown_path, metadata_path, markdown, metadata)

    def load_reflection_metadata(self, kind: str, period: str) -> dict[str, Any] | None:
        return self._load_json(self.reflection_metadata_path(kind, period))

    def iter_reflection_metadata(self):
        if not self.reflection_metadata_root.exists():
            return
        for kind_path in self.reflection_metadata_root.iterdir():
            if not kind_path.is_dir():
                continue
            for path in sorted(kind_path.glob("*.json")):
                data = self._load_json(path)
                if data is not None:
                    yield kind_path.name, path.stem, data

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

    def revision_path(self, date: str, revision_id: str) -> Path:
        self.validate_diary_date(date)
        self.validate_revision_id(revision_id)
        return self.revision_root / date / revision_id

    def correction_path(self, date: str, correction_id: str) -> Path:
        self.validate_diary_date(date)
        self.validate_correction_id(correction_id)
        return self.correction_root / date / f"{correction_id}.json"

    def revision_state_path(self, date: str) -> Path:
        self.validate_diary_date(date)
        return self.revision_state_root / f"{date}.json"

    @staticmethod
    def validate_diary_date(value: str) -> None:
        try:
            if date.fromisoformat(value).isoformat() != value:
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise ValueError("date must be ISO YYYY-MM-DD") from exc

    @staticmethod
    def validate_revision_id(value: str) -> None:
        if not re.fullmatch(r"rev_[0-9a-f]{32}", str(value)):
            raise ValueError("invalid revision id")

    @staticmethod
    def validate_correction_id(value: str) -> None:
        if not re.fullmatch(r"correction_[0-9a-f]{32}", str(value)):
            raise ValueError("invalid correction id")

    def load_revision_state(self, date: str) -> dict[str, Any]:
        state = self._load_json(self.revision_state_path(date)) or {"date": date, "current_revision_id": ""}
        current = str(state.get("current_revision_id") or "")
        if current:
            self._validate_same_date_revision(date, current)
        return state

    def save_revision_state(self, date: str, state: dict[str, Any]) -> None:
        self.validate_diary_date(date)
        if str(state.get("date") or date) != date:
            raise ValueError("revision state date does not match path")
        current = str(state.get("current_revision_id") or "")
        if current:
            self._validate_same_date_revision(date, current)
        atomic_write_json(self.revision_state_path(date), state)

    def create_revision(self, date: str, *, operation: str, correction_id: str = "", parent_revision_id: str | None = None, rollback_target_revision_id: str = "", set_current: bool = False) -> dict[str, Any]:
        self.validate_diary_date(date)
        if correction_id:
            self.validate_correction_id(correction_id)
        if parent_revision_id:
            self.validate_revision_id(parent_revision_id)
        if rollback_target_revision_id:
            self._validate_same_date_revision(date, rollback_target_revision_id)
        markdown_path, metadata_path = self.diary_path(date), self.metadata_path(date)
        if not markdown_path.is_file() or not metadata_path.is_file():
            raise FileNotFoundError(f"daily diary is incomplete: {date}")
        state = self.load_revision_state(date)
        revision_id = f"rev_{uuid.uuid4().hex}"
        parent = state.get("current_revision_id", "") if parent_revision_id is None else parent_revision_id
        if parent:
            self._validate_same_date_revision(date, str(parent))
        revision = {
            "id": revision_id, "date": date, "created_at": datetime.now(timezone.utc).isoformat(),
            "operation": operation, "parent_revision_id": str(parent or ""), "correction_id": correction_id,
            "rollback_target_revision_id": rollback_target_revision_id,
        }
        target = self.revision_path(date, revision_id)
        atomic_write_text(target / "diary.md", markdown_path.read_text(encoding="utf-8"))
        atomic_write_text(target / "metadata.json", metadata_path.read_text(encoding="utf-8"))
        atomic_write_json(target / "revision.json", revision)
        if set_current:
            state.update({"date": date, "current_revision_id": revision_id, "updated_at": revision["created_at"]})
            self.save_revision_state(date, state)
        return revision

    def load_revision(self, date: str, revision_id: str) -> dict[str, Any] | None:
        return self._load_json(self.revision_path(date, revision_id) / "revision.json")

    def _validate_same_date_revision(self, date: str, revision_id: str) -> None:
        self.validate_revision_id(revision_id)
        revision = self.load_revision(date, revision_id)
        if revision is None or str(revision.get("date") or "") != date:
            raise ValueError("revision does not exist for this diary date")

    def delete_revision(self, date: str, revision_id: str) -> None:
        target = self.revision_path(date, revision_id)
        root = (self.revision_root / date).resolve()
        if target.resolve().parent != root:
            raise ValueError("invalid revision path")
        shutil.rmtree(target, ignore_errors=True)

    def load_revision_contents(self, date: str, revision_id: str) -> tuple[str, dict[str, Any]]:
        root = self.revision_path(date, revision_id)
        try:
            markdown = (root / "diary.md").read_text(encoding="utf-8")
        except OSError as exc:
            raise FileNotFoundError(f"missing revision markdown: {revision_id}") from exc
        metadata = self._load_json(root / "metadata.json")
        if metadata is None:
            raise FileNotFoundError(f"missing revision metadata: {revision_id}")
        return markdown, metadata

    def iter_revisions(self, date: str):
        self.validate_diary_date(date)
        root = self.revision_root / date
        if not root.exists():
            return
        for path in sorted(root.iterdir()):
            data = self._load_json(path / "revision.json") if path.is_dir() else None
            if data is not None:
                yield data

    def save_correction(self, date: str, correction: dict[str, Any]) -> None:
        self.validate_diary_date(date)
        self.validate_correction_id(str(correction["id"]))
        if str(correction.get("date") or date) != date:
            raise ValueError("correction date does not match path")
        atomic_write_json(self.correction_path(date, str(correction["id"])), correction)

    def load_correction(self, date: str, correction_id: str) -> dict[str, Any] | None:
        return self._load_json(self.correction_path(date, correction_id))

    def delete_correction(self, date: str, correction_id: str) -> None:
        target = self.correction_path(date, correction_id)
        root = (self.correction_root / date).resolve()
        if target.resolve().parent != root:
            raise ValueError("invalid correction path")
        target.unlink(missing_ok=True)

    def iter_corrections(self, date: str):
        self.validate_diary_date(date)
        root = self.correction_root / date
        if not root.exists():
            return
        for path in sorted(root.glob("*.json")):
            data = self._load_json(path)
            if data is not None:
                yield data

    def update_correction(self, date: str, correction_id: str, **changes: Any) -> dict[str, Any] | None:
        correction = self.load_correction(date, correction_id)
        if correction is None:
            return None
        correction.update(changes)
        atomic_write_json(self.correction_path(date, correction_id), correction)
        return correction

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

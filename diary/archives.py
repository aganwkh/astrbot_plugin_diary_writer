"""Portable, verified diary archives using only the Python standard library."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

from .maintenance import GLOBAL_MAINTENANCE_GATE, MaintenanceGate
from .storage import DiaryStorage


class ArchiveError(ValueError):
    pass


ARCHIVE_FORMAT = 1
MAX_FILES = 10_000
MAX_TOTAL_SIZE = 512 * 1024 * 1024
MAX_FILE_SIZE = 64 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100
PRE_RESTORE_KEEP = 5
_TREE_ROOTS = (
    "diaries", "metadata", "reviews", "review_metadata", "revisions", "corrections",
    "revision_state", "reflections", "reflection_metadata", "daily_activity",
)
_STATE_FILES = (
    "continuity.json", "generation_state.json", "review_generation_state.json",
    "reminder_state.json", "activity.json", "reflection_generation_state.json",
    "private_session_ids.json", "reflection_usage.json", "daily_finalization_state.json",
)
_SAFE_SETTINGS = (
    "auto_write_enabled", "inactive_minutes", "fallback_inactive_minutes", "cron_start_delay_minutes",
    "on_this_day_reminder_enabled", "low_activity_round_threshold", "sparse_memory_threshold",
    "recent_context_days", "historical_memory_min_count", "historical_memory_max_count", "reflection_cooldown_days",
)
_DRIVE_PATH = re.compile(r"^[A-Za-z]:")
_RESERVED_WINDOWS = {"CON", "PRN", "AUX", "NUL", *(f"COM{value}" for value in range(1, 10)), *(f"LPT{value}" for value in range(1, 10))}
_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9_.-]{1,160}")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_settings(value: Mapping[str, Any] | None) -> dict[str, Any]:
    value = value or {}
    return {key: value[key] for key in _SAFE_SETTINGS if key in value}


class ArchiveService:
    """Exports and restores only the diary data roots, never host configuration."""

    def __init__(self, storage: DiaryStorage, settings: Mapping[str, Any] | None = None, gate: MaintenanceGate | None = None):
        self.storage = storage
        self.settings = dict(settings or {})
        self.gate = gate or GLOBAL_MAINTENANCE_GATE
        self.export_root = storage.root / "archive_exports"
        self.pre_restore_root = storage.root / "pre_restore_snapshots"

    async def export(self, destination: Path | None = None) -> Path:
        """Write a manifest-checked ZIP.  Historic ZIPs are never input files."""
        async with self.gate.snapshot():
            return self._export_unlocked(destination)

    def _export_unlocked(self, destination: Path | None = None) -> Path:
        self.export_root.mkdir(parents=True, exist_ok=True)
        if destination is None:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            destination = self.export_root / f"diary-export-{stamp}.zip"
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise ArchiveError("archive destination already exists")
        files = list(self._source_files())
        settings_temp: Path | None = None
        settings = _safe_settings(self.settings)
        if settings:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".json") as stream:
                json.dump(settings, stream, ensure_ascii=False, indent=2, sort_keys=True)
                stream.write("\n")
                settings_temp = Path(stream.name)
            files.append(("settings.json", settings_temp))
        manifest_files = [{"path": name, "size": path.stat().st_size, "sha256": _sha256_file(path)} for name, path in files]
        manifest = {
            "format_version": ARCHIVE_FORMAT,
            "plugin_version": "1.1.2",
            "created_at": _utc_now(),
            "plugin_data_format": "v0.3-v1.1",
            "settings_restore_policy": "exported_safe_settings_are_not_restored",
            "files": manifest_files,
        }
        temporary = destination.with_name(f".{destination.name}.tmp")
        try:
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
                for name, path in files:
                    archive.write(path, name)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
            if settings_temp is not None:
                settings_temp.unlink(missing_ok=True)
        return destination

    def verify(self, archive_path: Path) -> dict[str, Any]:
        """Read and checksum an archive without extracting it."""
        archive_path = Path(archive_path)
        try:
            with zipfile.ZipFile(archive_path, "r") as archive:
                infos = archive.infolist()
                self._validate_infos(infos)
                names = [info.filename for info in infos]
                if names.count("manifest.json") != 1:
                    raise ArchiveError("archive must contain exactly one manifest")
                manifest = self._read_json(archive.read("manifest.json"), "manifest")
                self._validate_manifest(manifest, names)
                expected = {str(item["path"]): item for item in manifest["files"]}
                for name, entry in expected.items():
                    info = archive.getinfo(name)
                    data = archive.read(info)
                    if len(data) != int(entry["size"]) or hashlib.sha256(data).hexdigest() != entry["sha256"]:
                        raise ArchiveError(f"checksum mismatch: {name}")
        except (OSError, zipfile.BadZipFile, KeyError, RuntimeError) as exc:
            if isinstance(exc, ArchiveError):
                raise
            raise ArchiveError(f"invalid archive: {exc}") from exc
        return manifest

    async def restore(self, archive_path: Path, *, dry_run: bool = False) -> dict[str, Any]:
        """Validate, stage, snapshot and restore.  Any failed apply is rolled back."""
        manifest = self.verify(archive_path)
        result = {"ok": True, "dry_run": dry_run, "files": len(manifest["files"]), "settings_ignored": "settings.json" in {x["path"] for x in manifest["files"]}, "settings_restore_policy": "exported_safe_settings_are_not_restored"}
        if dry_run:
            return result
        async with self.gate.restore():
            with tempfile.TemporaryDirectory(prefix="diary-restore-", dir=self.storage.root.parent) as workspace:
                workspace_path = Path(workspace)
                candidate = workspace_path / "candidate.zip"
                # Freeze the bytes before the second validation so a concurrent
                # replacement of a user-selected archive cannot create a TOCTOU
                # gap between verification and extraction.
                shutil.copy2(archive_path, candidate)
                manifest = self.verify(candidate)
                staging = workspace_path / "staging"
                rollback = workspace_path / "rollback"
                self._extract_validated(candidate, staging)
                self._copy_selected(self.storage.root, rollback)
                snapshot = await self._pre_restore_snapshot()
                try:
                    self._apply_selected(staging)
                except Exception as exc:
                    try:
                        self._apply_selected(rollback)
                    except Exception as rollback_error:
                        raise ArchiveError(f"restore failed and rollback failed: {rollback_error}") from exc
                    raise ArchiveError(f"restore failed; original data restored: {exc}") from exc
        # Browser callers can use the filename without learning a server path.
        result["pre_restore_snapshot"] = snapshot.name
        return result

    def _source_files(self) -> Iterable[tuple[str, Path]]:
        for root_name in _TREE_ROOTS:
            root = self.storage.root / root_name
            if not root.is_dir():
                continue
            for path in sorted(root.rglob("*")):
                if path.is_file() and not path.is_symlink():
                    name = path.relative_to(self.storage.root).as_posix()
                    if self._valid_data_shape(PurePosixPath(name)):
                        yield name, path
        for name in _STATE_FILES:
            path = self.storage.root / name
            if path.is_file() and not path.is_symlink():
                yield name, path

    @staticmethod
    def _read_json(raw: bytes, label: str) -> dict[str, Any]:
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ArchiveError(f"invalid {label} json") from exc
        if not isinstance(value, dict):
            raise ArchiveError(f"invalid {label}")
        return value

    def _validate_infos(self, infos: list[zipfile.ZipInfo]) -> None:
        if len(infos) > MAX_FILES + 1:
            raise ArchiveError("archive has too many files")
        total = 0
        seen: set[str] = set()
        seen_casefolded: set[str] = set()
        for info in infos:
            name = self._safe_member_name(info.filename)
            if name in seen or name.casefold() in seen_casefolded:
                raise ArchiveError(f"duplicate archive path: {name}")
            seen.add(name); seen_casefolded.add(name.casefold())
            mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(mode):
                raise ArchiveError("symlink archive members are forbidden")
            if info.is_dir():
                raise ArchiveError("directory archive members are forbidden")
            if info.file_size > MAX_FILE_SIZE:
                raise ArchiveError("archive member is too large")
            total += info.file_size
            if total > MAX_TOTAL_SIZE:
                raise ArchiveError("archive uncompressed size is too large")
            if info.file_size and (info.compress_size == 0 or info.file_size / info.compress_size > MAX_COMPRESSION_RATIO):
                raise ArchiveError("archive compression ratio is too high")

    def _validate_manifest(self, manifest: dict[str, Any], archive_names: list[str]) -> None:
        if manifest.get("format_version") != ARCHIVE_FORMAT or not isinstance(manifest.get("files"), list):
            raise ArchiveError("unsupported archive manifest")
        files = manifest["files"]
        if len(files) > MAX_FILES:
            raise ArchiveError("manifest has too many files")
        expected: set[str] = set()
        expected_casefolded: set[str] = set()
        total = 0
        for entry in files:
            if not isinstance(entry, dict):
                raise ArchiveError("invalid manifest entry")
            name = self._safe_member_name(str(entry.get("path") or ""))
            size, digest = entry.get("size"), entry.get("sha256")
            if name in expected or name.casefold() in expected_casefolded or not isinstance(size, int) or size < 0 or size > MAX_FILE_SIZE or not re.fullmatch(r"[0-9a-f]{64}", str(digest)):
                raise ArchiveError("invalid manifest entry")
            expected.add(name); expected_casefolded.add(name.casefold()); total += size
            if total > MAX_TOTAL_SIZE:
                raise ArchiveError("manifest size is too large")
        actual = set(archive_names) - {"manifest.json"}
        if expected != actual:
            raise ArchiveError("archive members do not match manifest")

    @staticmethod
    def _safe_member_name(name: str) -> str:
        if not name or "\\" in name or name.startswith("/") or _DRIVE_PATH.match(name):
            raise ArchiveError("unsafe archive path")
        if any(part in ("", ".", "..") for part in name.split("/")):
            raise ArchiveError("unsafe archive path")
        path = PurePosixPath(name)
        if path.is_absolute() or path.as_posix() != name:
            raise ArchiveError("unsafe archive path")
        normal = path.as_posix()
        if any(ArchiveService._windows_reserved(part) for part in path.parts):
            raise ArchiveError("unsafe Windows archive path")
        head = path.parts[0]
        if normal == "manifest.json":
            return normal
        if normal == "settings.json":
            return normal
        if head not in _TREE_ROOTS and normal not in _STATE_FILES:
            raise ArchiveError("archive member is not in the data whitelist")
        if not ArchiveService._valid_data_shape(path):
            raise ArchiveError("archive member has an invalid data shape")
        return normal

    @staticmethod
    def _windows_reserved(part: str) -> bool:
        if part != part.rstrip(" ."):
            return True
        base = part.split(".", 1)[0].upper()
        return base in _RESERVED_WINDOWS

    @staticmethod
    def _valid_date(value: str) -> bool:
        try:
            return date.fromisoformat(value).isoformat() == value
        except ValueError:
            return False

    @classmethod
    def _valid_data_shape(cls, path: PurePosixPath) -> bool:
        parts = path.parts
        name = path.as_posix()
        if name in _STATE_FILES:
            return len(parts) == 1
        if len(parts) < 2:
            return False
        root = parts[0]
        if root in {"diaries", "metadata", "revision_state", "daily_activity"}:
            suffix = {"diaries": ".md", "metadata": ".json", "revision_state": ".json", "daily_activity": ".json"}[root]
            return len(parts) == 2 and parts[1].endswith(suffix) and cls._valid_date(parts[1][:-len(suffix)])
        if root in {"reviews", "review_metadata", "reflections", "reflection_metadata"}:
            suffix = ".json" if root.endswith("metadata") else ".md"
            if len(parts) != 3 or parts[1] not in {"weekly", "monthly", "yearly"} or not parts[2].endswith(suffix):
                return False
            period = parts[2][:-len(suffix)]
            patterns = {"weekly": r"\d{4}-W(?:0[1-9]|[1-4]\d|5[0-3])", "monthly": r"\d{4}-(?:0[1-9]|1[0-2])", "yearly": r"\d{4}"}
            return bool(re.fullmatch(patterns[parts[1]], period))
        if root == "corrections":
            return len(parts) == 3 and cls._valid_date(parts[1]) and parts[2].endswith(".json") and bool(_SAFE_IDENTIFIER.fullmatch(parts[2][:-5]))
        if root == "revisions":
            return len(parts) == 4 and cls._valid_date(parts[1]) and bool(_SAFE_IDENTIFIER.fullmatch(parts[2])) and parts[3] in {"diary.md", "metadata.json", "revision.json"}
        return False

    def _extract_validated(self, archive_path: Path, staging: Path) -> None:
        with zipfile.ZipFile(archive_path, "r") as archive:
            for info in archive.infolist():
                name = self._safe_member_name(info.filename)
                if name in {"manifest.json", "settings.json"}:
                    continue
                target = staging.joinpath(*PurePosixPath(name).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)

    async def _pre_restore_snapshot(self) -> Path:
        self.pre_restore_root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        # restore() already owns the same gate, so do not recursively acquire it.
        result = self._export_unlocked(self.pre_restore_root / f"pre-restore-{stamp}.zip")
        snapshots = sorted(self.pre_restore_root.glob("pre-restore-*.zip"), key=lambda item: item.stat().st_mtime, reverse=True)
        for old in snapshots[PRE_RESTORE_KEEP:]:
            old.unlink(missing_ok=True)
        return result

    def _copy_selected(self, source: Path, destination: Path) -> None:
        for name in _TREE_ROOTS:
            root = source / name
            if root.is_dir():
                shutil.copytree(root, destination / name, symlinks=False)
        for name in _STATE_FILES:
            path = source / name
            if path.is_file():
                target = destination / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)

    def _apply_selected(self, source: Path) -> None:
        # `settings.json` deliberately never reaches this method.
        for name in _TREE_ROOTS:
            target = self.storage.root / name
            staged = source / name
            if target.exists():
                shutil.rmtree(target)
            if staged.is_dir():
                shutil.copytree(staged, target, symlinks=False)
        for name in _STATE_FILES:
            target = self.storage.root / name
            staged = source / name
            target.unlink(missing_ok=True)
            if staged.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(staged, target)

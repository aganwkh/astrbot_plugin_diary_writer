from datetime import datetime, timezone
from pathlib import Path

from .models import DiaryMetadata
from .storage import DiaryStorage, atomic_write_text


def migrate_legacy_markdown(storage: DiaryStorage, date: str) -> bool:
    """Add metadata for a v0.2 Markdown diary without touching its source text."""
    if not storage.diary_path(date).is_file() or storage.metadata_path(date).exists():
        return False
    title = storage.diary_path(date).read_text(encoding="utf-8").splitlines()[0].lstrip("# ").strip() or date
    storage.write_diary(date, storage.diary_path(date).read_text(encoding="utf-8"), DiaryMetadata(date=date, title=title, generated_at=datetime.now(timezone.utc).isoformat(), prompt_version="legacy-v0.2"))
    return True


def migrate_legacy_directory(legacy_diary_root, storage: DiaryStorage) -> int:
    """Copy v0.2 Markdown files into v0.3 storage and preserve the originals."""
    legacy_diary_root = Path(legacy_diary_root)
    if not legacy_diary_root.is_dir():
        return 0
    migrated = 0
    for source_path in legacy_diary_root.glob("*.md"):
        date = source_path.stem
        if storage.has_any_diary(date):
            continue
        atomic_write_text(storage.diary_path(date), source_path.read_text(encoding="utf-8"))
        if migrate_legacy_markdown(storage, date):
            migrated += 1
    return migrated

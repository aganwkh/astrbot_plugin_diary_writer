from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, (list, tuple, set)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


@dataclass(frozen=True)
class DiaryConfig:
    owner_ids: tuple[str, ...] = ()
    allow_group_commands: tuple[str, ...] = ("日记状态",)
    auto_write_enabled: bool = True
    livingmemory_db_path: str = ""
    generation_provider_id: str = ""
    diary_main_prompt: str = ""
    website_sync_enabled: bool = False
    website_sync_path: str = ""
    provider_retry_count: int = 2
    inactive_minutes: int = 90
    fallback_inactive_minutes: int = 60
    cron_start_delay_minutes: int = 30
    on_this_day_reminder_enabled: bool = False
    low_activity_round_threshold: int = 2
    sparse_memory_threshold: int = 2
    recent_context_days: int = 3
    historical_memory_min_count: int = 3
    historical_memory_max_count: int = 5
    reflection_cooldown_days: int = 30

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> "DiaryConfig":
        raw = raw or {}
        return cls(
            owner_ids=tuple(_string_list(raw.get("owner_ids", []))),
            allow_group_commands=tuple(_string_list(raw.get("allow_group_commands", ["日记状态"]))),
            auto_write_enabled=bool(raw.get("auto_write_enabled", True)),
            livingmemory_db_path=str(raw.get("livingmemory_db_path", "") or "").strip(),
            generation_provider_id=str(raw.get("generation_provider_id", "") or "").strip(),
            diary_main_prompt=str(raw.get("diary_main_prompt", "") or "").strip(),
            website_sync_enabled=bool(raw.get("website_sync_enabled", False)),
            website_sync_path=str(raw.get("website_sync_path", "") or "").strip(),
            provider_retry_count=max(0, min(5, int(raw.get("provider_retry_count", 2) or 0))),
            inactive_minutes=max(1, int(raw.get("inactive_minutes", 90) or 90)),
            fallback_inactive_minutes=max(1, int(raw.get("fallback_inactive_minutes", 60) or 60)),
            cron_start_delay_minutes=max(0, min(59, int(raw.get("cron_start_delay_minutes", 30) or 0))),
            on_this_day_reminder_enabled=bool(raw.get("on_this_day_reminder_enabled", False)),
            low_activity_round_threshold=max(0, min(20, int(raw.get("low_activity_round_threshold", 2) or 0))),
            sparse_memory_threshold=max(0, int(raw.get("sparse_memory_threshold", 2) or 0)),
            recent_context_days=max(1, min(7, int(raw.get("recent_context_days", 3) or 3))),
            historical_memory_min_count=max(1, min(5, int(raw.get("historical_memory_min_count", 3) or 3))),
            historical_memory_max_count=max(1, min(5, int(raw.get("historical_memory_max_count", 5) or 5))),
            reflection_cooldown_days=max(1, min(365, int(raw.get("reflection_cooldown_days", 30) or 30))),
        )

    def livingmemory_path(self, data_root: Path) -> Path:
        if self.livingmemory_db_path:
            return Path(self.livingmemory_db_path).expanduser()
        return data_root / "plugin_data" / "astrbot_plugin_livingmemory" / "livingmemory.db"

    @property
    def can_auto_write(self) -> bool:
        return self.auto_write_enabled and bool(self.owner_ids)

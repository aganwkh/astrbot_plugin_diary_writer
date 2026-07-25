from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class Persona:
    name: str
    voice: str


PRESETS = {
    "chihaya_anon": Persona(
        name="千早爱音",
        voice="用第一人称书写，自然、温暖，带一点轻松吐槽和小得意；不是工作报告。",
    ),
    "factual": Persona(
        name="",
        voice="使用克制、清晰的第一人称事实日记口吻，不虚构情节或情绪。",
    ),
}


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
    persona_preset: str = "chihaya_anon"
    persona_name: str = ""
    user_nickname: str = "虾仁"
    diary_voice: str = ""
    website_sync_enabled: bool = False
    website_sync_path: str = ""
    provider_retry_count: int = 2
    inactive_minutes: int = 90
    fallback_inactive_minutes: int = 60
    cron_start_delay_minutes: int = 30

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> "DiaryConfig":
        raw = raw or {}
        preset = str(raw.get("persona_preset", "chihaya_anon") or "chihaya_anon")
        return cls(
            owner_ids=tuple(_string_list(raw.get("owner_ids", []))),
            allow_group_commands=tuple(_string_list(raw.get("allow_group_commands", ["日记状态"]))),
            auto_write_enabled=bool(raw.get("auto_write_enabled", True)),
            livingmemory_db_path=str(raw.get("livingmemory_db_path", "") or "").strip(),
            generation_provider_id=str(raw.get("generation_provider_id", "") or "").strip(),
            persona_preset=preset if preset in PRESETS else "factual",
            persona_name=str(raw.get("persona_name", "") or "").strip(),
            user_nickname=str(raw.get("user_nickname", "虾仁") or "").strip(),
            diary_voice=str(raw.get("diary_voice", "") or "").strip(),
            website_sync_enabled=bool(raw.get("website_sync_enabled", False)),
            website_sync_path=str(raw.get("website_sync_path", "") or "").strip(),
            provider_retry_count=max(0, min(5, int(raw.get("provider_retry_count", 2) or 0))),
            inactive_minutes=max(1, int(raw.get("inactive_minutes", 90) or 90)),
            fallback_inactive_minutes=max(1, int(raw.get("fallback_inactive_minutes", 60) or 60)),
            cron_start_delay_minutes=max(0, min(59, int(raw.get("cron_start_delay_minutes", 30) or 0))),
        )

    @property
    def persona(self) -> Persona:
        preset = PRESETS[self.persona_preset]
        return Persona(self.persona_name or preset.name, self.diary_voice or preset.voice)

    def livingmemory_path(self, data_root: Path) -> Path:
        if self.livingmemory_db_path:
            return Path(self.livingmemory_db_path).expanduser()
        return data_root / "plugin_data" / "astrbot_plugin_livingmemory" / "livingmemory.db"

    @property
    def can_auto_write(self) -> bool:
        return self.auto_write_enabled and bool(self.owner_ids)

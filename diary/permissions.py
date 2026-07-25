from __future__ import annotations

from typing import Any

from .config import DiaryConfig


SENSITIVE_COMMANDS = frozenset({"查看日记", "测试日记", "补写日记", "重写日记", "补写周记", "重写周记", "补写月记", "重写月记", "问日记", "那年今日", "日记项目", "日记话题"})
PRIVATE_TYPES = frozenset({"friendmessage", "private", "privatemessage", "private_message", "direct"})


def sender_id(event: Any) -> str:
    try:
        return str(event.get_sender_id())
    except Exception:
        return ""


def is_authorized(event: Any, config: DiaryConfig) -> bool:
    return bool(config.owner_ids) and sender_id(event) in config.owner_ids


def is_private_event(event: Any) -> bool:
    origin = str(getattr(event, "unified_msg_origin", "") or "")
    parts = [part.lower() for part in origin.split(":")]
    if len(parts) >= 3 and parts[1] in PRIVATE_TYPES:
        return True
    message_obj = getattr(event, "message_obj", None)
    token = str(getattr(message_obj, "type", "") or getattr(event, "message_type", "")).lower()
    return token in PRIVATE_TYPES or token.endswith(".friend_message")


def can_access_sensitive_diary(event: Any, config: DiaryConfig) -> bool:
    return is_authorized(event, config) and is_private_event(event)


def can_use_group_command(event: Any, config: DiaryConfig, command: str) -> bool:
    return (
        command not in SENSITIVE_COMMANDS
        and command in config.allow_group_commands
        and is_authorized(event, config)
    )


def private_only_reminder() -> str:
    return "日记内容仅限授权用户在私聊中查看。"

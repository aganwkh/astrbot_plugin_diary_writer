from __future__ import annotations

from typing import Any

from .retrieval import search_daily
from .storage import DiaryStorage


def _references(items) -> str:
    return "\n".join(f"- {item.date}｜{item.title}：{item.summary}" for item in items)


async def ask_diary(storage: DiaryStorage, question: str, provider: Any | None) -> str:
    matches = search_daily(storage, question)
    if not matches:
        return "未检索到相关日记。"
    references = _references(matches)
    return f"找到以下日记来源：\n{references}"

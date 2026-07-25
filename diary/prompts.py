from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .config import DiaryConfig
from .models import ContinuityState, DiaryEvent, DiaryMetadata, as_jsonable


PROMPT_VERSION = "v1"


@dataclass
class ParsedDiary:
    markdown: str
    metadata: DiaryMetadata


def _list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))


def build_messages(date: str, events: list[DiaryEvent], continuity: ContinuityState, config: DiaryConfig) -> tuple[str, str]:
    persona = config.persona
    nickname = config.user_nickname or "我"
    system = f"""你是一个严格依据证据写日记的助手。角色名：{persona.name or '未设置'}；写作对象昵称：{nickname}。
口吻：{persona.voice}
只能陈述提供的 event.facts 所支持的事实。无法确认的内容必须放进事件的 inferences，且明确为推测；不要补造人物、项目、时间、对话或结果。
只返回 JSON 对象，不要 Markdown 代码围栏。字段必须包含 markdown、title、mood、mood_score、topics、tags、people、projects、events、highlights、unresolved、ongoing_topics。每个 events 项必须包含 summary、memory_ids、facts、inferences、topics、time_range。"""
    material = {
        "date": date,
        "prompt_version": PROMPT_VERSION,
        "events": as_jsonable(events),
        "continuity": as_jsonable(continuity),
    }
    return system, json.dumps(material, ensure_ascii=False, indent=2)


def parse_diary_response(raw: str, date: str, allowed_memory_ids: set[str]) -> ParsedDiary:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("provider did not return a JSON diary envelope") from exc
    if not isinstance(data, dict) or not isinstance(data.get("markdown"), str) or not data["markdown"].strip():
        raise ValueError("provider response has no diary markdown")

    events: list[DiaryEvent] = []
    for raw_event in data.get("events", []):
        if not isinstance(raw_event, dict):
            continue
        memory_ids = [memory_id for memory_id in _list(raw_event.get("memory_ids")) if memory_id in allowed_memory_ids]
        if not memory_ids:
            continue
        events.append(
            DiaryEvent(
                summary=str(raw_event.get("summary") or "").strip()[:500],
                memory_ids=memory_ids,
                kind=str(raw_event.get("kind") or "event").strip()[:80],
                facts=_list(raw_event.get("facts")),
                inferences=_list(raw_event.get("inferences")),
                topics=_list(raw_event.get("topics")),
                time_range=_list(raw_event.get("time_range"))[:2],
            )
        )
    try:
        mood_score = float(data["mood_score"]) if data.get("mood_score") is not None else None
    except (TypeError, ValueError):
        mood_score = None
    used_ids = list(dict.fromkeys(memory_id for event in events for memory_id in event.memory_ids))
    metadata = DiaryMetadata(
        date=date,
        title=str(data.get("title") or date).strip()[:200],
        mood=str(data.get("mood") or "").strip()[:120],
        mood_score=mood_score,
        topics=_list(data.get("topics")), tags=_list(data.get("tags")), people=_list(data.get("people")),
        projects=_list(data.get("projects")), events=events, highlights=_list(data.get("highlights")),
        unresolved=_list(data.get("unresolved")), ongoing_topics=_list(data.get("ongoing_topics")),
        memory_ids=used_ids, source_count=len(allowed_memory_ids),
        generated_at=datetime.now(timezone.utc).isoformat(), prompt_version=PROMPT_VERSION,
    )
    return ParsedDiary(data["markdown"].strip() + "\n", metadata)

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .config import DiaryConfig
from .models import ContinuityState, DiaryEvent, DiaryMetadata, as_jsonable


UNIFIED_PROMPT_VERSION = "v1.1.2-unified"
MODE_CONTRACT_VERSION = "v1"
NORMAL_PROMPT_VERSION = UNIFIED_PROMPT_VERSION
ADAPTIVE_PROMPT_VERSION = UNIFIED_PROMPT_VERSION
PROMPT_VERSION = UNIFIED_PROMPT_VERSION


MAIN_DIARY_PROMPT = """请保持并完全遵循 AstrBot 当前选中的人格，以这个人格自己的第一人称写私人日记。

人物身份、称呼、关系、性格、语气和表达习惯均以 AstrBot 人格为准；这里不另设角色，也不要把人格中的自己写成旁观者。

写日记时按你自己的关注点选择内容：有感觉的事情可以多写，没感觉的可以略过。比起完整记录“发生了什么”，更在意这些事情让你想到什么、产生了什么情绪。

文字应当像这个人格在一天结束时自然写下来的私人记录，而不是工作报告或第三人称总结。可以偏心、跑题、自言自语和自由联想，但不能为了文风改变素材中的客观事实与日期。"""


OUTPUT_CONTRACT = """

输入 JSON 中的 mode_contract 是素材使用契约，必须严格遵守：
- today_fact_sources 才能支持“当天发生”的确定事实和 structured events。
- context_only_sources 只能用于正文中的背景、回忆、联想、情绪或延续；除非同一事实也有 today_fact_sources 证据，否则不能写入 structured events。
- preserve_original_date_for 中的素材必须保留原日期语义，不能改写成日记当天发生。
- required_usage 中标记的最低使用数量必须满足；不要求使用全部候选，也不要机械拼接。
- continuity 只是长期状态提示，不能单独证明新的事实。
- 主观感受可以自由表达；无依据的猜测必须使用不确定语气，不能制造人物、地点、对话、结果或新的结构化事实。
- 上述事实、证据和日期规则同时约束 markdown 正文与 structured events；文风自由不等于客观事实可以自由补写。
- today_events 中如果明确提到其他日期，正文仍须保留该日期，不能因为它位于 today_events 就改写成当天发生。
- continuity 只能引出想法、疑问、期待或带有原日期语义的回顾；没有 today_fact_sources 佐证时，不得据此断言某件事当前仍然成立。

只返回 JSON 对象，不要 Markdown 代码围栏。字段必须包含 markdown、title、mood、mood_score、topics、tags、people、projects、events、highlights、unresolved、ongoing_topics、used_recent_memory_ids、used_historical_memory_ids。每个 events 项必须包含 summary、memory_ids、facts、inferences、topics、time_range。events 只能引用 today_events 中的 memory_ids；used_recent_memory_ids 和 used_historical_memory_ids 只能列出实际写进正文且存在于对应候选中的 ID，未使用就返回空数组。"""


@dataclass
class ParsedDiary:
    markdown: str
    metadata: DiaryMetadata
    used_recent_memory_ids: list[str]
    used_historical_memory_ids: list[str]


def _list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))


def _system_prompt(astrbot_persona_prompt: str = "") -> str:
    persona = str(astrbot_persona_prompt or "").strip()
    sections = []
    if persona:
        sections.append(f"# AstrBot 当前人格\n\n{persona}")
    sections.append(f"# 私人日记任务\n\n{MAIN_DIARY_PROMPT}{OUTPUT_CONTRACT}")
    return "\n\n".join(sections)


def _mode_contract(entry_type: str, recent_context_sources: list[dict], historical_memory_sources: list[dict]) -> dict[str, Any]:
    has_recent = any(str(item.get("memory_id") or "") for item in recent_context_sources if isinstance(item, dict))
    has_historical = any(str(item.get("memory_id") or "") for item in historical_memory_sources if isinstance(item, dict))
    context_sources = ["conversation_sources", "continuity"]
    forbidden_sources: list[str] = []
    required_usage: dict[str, dict[str, int]] = {}
    preserve_dates: list[str] = []
    if entry_type == "normal":
        forbidden_sources = ["recent_context_sources", "historical_memory_sources"]
    elif entry_type == "sparse":
        context_sources.append("recent_context_sources")
        forbidden_sources = ["historical_memory_sources"]
        preserve_dates.append("recent_context_sources")
        if has_recent:
            required_usage["recent_context_sources"] = {"minimum_used": 1}
    elif entry_type == "low_activity":
        context_sources.extend(["recent_context_sources", "historical_memory_sources"])
        preserve_dates.extend(["recent_context_sources", "historical_memory_sources"])
        if has_recent or has_historical:
            required_usage["recent_or_historical"] = {"minimum_used": 1}
    else:
        raise ValueError("unsupported diary entry type")
    return {
        "version": MODE_CONTRACT_VERSION,
        "mode": entry_type,
        "today_fact_sources": ["today_events"],
        "context_only_sources": context_sources,
        "forbidden_sources": forbidden_sources,
        "preserve_original_date_for": preserve_dates,
        "required_usage": required_usage,
    }


def _material(
    date: str,
    entry_type: str,
    events: list[DiaryEvent],
    continuity: ContinuityState,
    conversation_sources: list[dict],
    recent_context_sources: list[dict],
    historical_memory_sources: list[dict],
) -> dict[str, Any]:
    material = {
        "date": date,
        "entry_type": entry_type,
        "prompt_version": UNIFIED_PROMPT_VERSION,
        "mode_contract": _mode_contract(entry_type, recent_context_sources, historical_memory_sources),
        "today_events": as_jsonable(events),
        "conversation_sources": conversation_sources,
        "continuity": as_jsonable(continuity),
    }
    if entry_type in {"sparse", "low_activity"}:
        material["recent_context_sources"] = recent_context_sources
    if entry_type == "low_activity":
        material["historical_memory_sources"] = historical_memory_sources
    return material


def build_messages(
    date: str, events: list[DiaryEvent], continuity: ContinuityState, config: DiaryConfig,
    conversation_sources: list[dict] | None = None,
    astrbot_persona_prompt: str = "",
) -> tuple[str, str]:
    material = _material(date, "normal", events, continuity, conversation_sources or [], [], [])
    return _system_prompt(astrbot_persona_prompt), json.dumps(material, ensure_ascii=False, indent=2)


def build_adaptive_messages(
    date: str, entry_type: str, events: list[DiaryEvent], continuity: ContinuityState, config: DiaryConfig,
    conversation_sources: list[dict], recent_context_sources: list[dict], historical_memory_sources: list[dict],
    astrbot_persona_prompt: str = "",
) -> tuple[str, str]:
    material = _material(
        date, entry_type, events, continuity, conversation_sources,
        recent_context_sources, historical_memory_sources,
    )
    return _system_prompt(astrbot_persona_prompt), json.dumps(material, ensure_ascii=False, indent=2)


def parse_diary_response(
    raw: str,
    date: str,
    allowed_memory_ids: set[str],
    historical_candidate_ids: set[str] | None = None,
    recent_candidate_ids: set[str] | None = None,
    prompt_version: str = NORMAL_PROMPT_VERSION,
) -> ParsedDiary:
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
        generated_at=datetime.now(timezone.utc).isoformat(), prompt_version=prompt_version,
    )
    recent = [memory_id for memory_id in _list(data.get("used_recent_memory_ids")) if memory_id in (recent_candidate_ids or set())]
    historical = [memory_id for memory_id in _list(data.get("used_historical_memory_ids")) if memory_id in (historical_candidate_ids or set())]
    return ParsedDiary(
        data["markdown"].strip() + "\n",
        metadata,
        list(dict.fromkeys(recent)),
        list(dict.fromkeys(historical)),
    )

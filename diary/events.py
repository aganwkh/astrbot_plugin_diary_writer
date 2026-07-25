from __future__ import annotations

import re
from datetime import timedelta

from .models import DiaryEvent, SourceMemory


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]{2,}", text.lower()))


def _similar(left: SourceMemory, right: SourceMemory) -> bool:
    left_topics, right_topics = set(left.topics), set(right.topics)
    if left_topics & right_topics and abs(left.occurred_at - right.occurred_at) <= timedelta(hours=4):
        return True
    left_tokens, right_tokens = _tokens(left.text), _tokens(right.text)
    if not left_tokens or not right_tokens:
        return False
    overlap = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
    return overlap >= 0.45 and abs(left.occurred_at - right.occurred_at) <= timedelta(hours=6)


def cluster_memories(memories: list[SourceMemory]) -> list[DiaryEvent]:
    """Deduplicate adjacent source memories while retaining every evidence ID."""
    clusters: list[list[SourceMemory]] = []
    for memory in sorted(memories, key=lambda item: (item.occurred_at, item.memory_id)):
        if clusters and _similar(clusters[-1][-1], memory):
            clusters[-1].append(memory)
        else:
            clusters.append([memory])

    events: list[DiaryEvent] = []
    for cluster in clusters:
        first = cluster[0]
        topics = list(dict.fromkeys(topic for item in cluster for topic in item.topics))
        facts = list(dict.fromkeys(item.text for item in cluster))
        events.append(
            DiaryEvent(
                summary=first.text[:240],
                memory_ids=[item.memory_id for item in cluster],
                facts=facts,
                topics=topics,
                time_range=[cluster[0].occurred_at.isoformat(), cluster[-1].occurred_at.isoformat()],
            )
        )
    return events

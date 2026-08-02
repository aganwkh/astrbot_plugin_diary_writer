"""Small, durable private-message tracker for low-activity diary days."""
from __future__ import annotations

import asyncio
import random
from datetime import date

from .models import SourceMemory
from .storage import DiaryStorage


def classify_entry_type(memory_count: int) -> str:
    return "low_activity" if max(0, int(memory_count)) <= 4 else "normal"


def historical_weight(memory: SourceMemory, target_date: date, usage: dict[str, dict], cooldown_days: int = 30) -> float:
    age = max(0, (target_date - memory.occurred_at.date()).days)
    tier = 3.0 if 4 <= age <= 30 else 1.5 if age <= 180 else 0.6
    importance = 1.0 + min(2.0, max(0.0, float(memory.importance)) / 5.0)
    cooldown = usage.get(memory.memory_id, {}) if isinstance(usage, dict) else {}
    reflected_at = str(cooldown.get("last_reflected_at") or "")
    try:
        reflected_age = (target_date - date.fromisoformat(reflected_at[:10])).days
    except ValueError:
        reflected_age = cooldown_days + 1
    cooldown_factor = 0.15 if 0 <= reflected_age <= cooldown_days else 1.0
    return max(0.01, tier * importance * cooldown_factor)


def select_historical_memories(
    memories: list[SourceMemory], target_date: date, usage: dict[str, dict], private_session_ids: set[str], *, rng=None, minimum: int = 3, maximum: int = 5, cooldown_days: int = 30,
) -> list[SourceMemory]:
    pool = [item for item in memories if item.occurred_at.date() < target_date and item.session_id in private_session_ids]
    if not pool:
        return []
    chooser = rng or random.SystemRandom()
    lower, upper = sorted((max(1, minimum), max(1, maximum)))
    count = min(len(pool), chooser.randint(lower, upper))
    chosen: list[SourceMemory] = []
    while pool and len(chosen) < count:
        weights = [historical_weight(item, target_date, usage, cooldown_days) for item in pool]
        item = chooser.choices(pool, weights=weights, k=1)[0]
        chosen.append(item)
        pool.remove(item)
    return chosen


class DailyActivityTracker:
    def __init__(self, storage: DiaryStorage, saved_rounds: int = 2):
        self.storage = storage
        self.saved_rounds = max(0, saved_rounds)
        self._locks: dict[str, asyncio.Lock] = {}

    async def record(self, diary_date: date, timestamp: str, user_text: str, session_id: str) -> dict:
        date_text = diary_date.isoformat()
        async with self._locks.setdefault(date_text, asyncio.Lock()):
            activity = self.storage.load_daily_activity(date_text)
            count = max(0, int(activity.get("round_count") or 0)) + 1
            activity["date"] = date_text
            activity["round_count"] = count
            sources = activity.get("conversation_sources") if isinstance(activity.get("conversation_sources"), list) else []
            if count <= self.saved_rounds:
                sources.append({"timestamp": str(timestamp), "user_text": str(user_text).strip()})
            activity["conversation_sources"] = sources[: self.saved_rounds]
            sessions = activity.get("private_session_ids") if isinstance(activity.get("private_session_ids"), list) else []
            if session_id in sessions:
                sessions.remove(session_id)
            if session_id:
                sessions.append(session_id)
            activity["private_session_ids"] = sessions
            self.storage.save_daily_activity(date_text, activity)
            known_sessions = self.storage.load_private_session_ids()
            self.storage.save_private_session_ids(known_sessions + sessions)
            return activity

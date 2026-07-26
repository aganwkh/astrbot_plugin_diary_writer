from datetime import datetime


def should_generate(now: datetime, last_active: datetime | None, minutes: int) -> bool:
    if last_active is None:
        return False
    now_aware = now.tzinfo is not None and now.utcoffset() is not None
    active_aware = last_active.tzinfo is not None and last_active.utcoffset() is not None
    if active_aware and not now_aware:
        now = now.replace(tzinfo=last_active.tzinfo)
    elif now_aware and not active_aware:
        last_active = last_active.replace(tzinfo=now.tzinfo)
    return (now - last_active).total_seconds() >= minutes * 60


def should_run_regular_check(now: datetime, start_delay_minutes: int = 30) -> bool:
    return now.hour != 0 or now.minute >= start_delay_minutes

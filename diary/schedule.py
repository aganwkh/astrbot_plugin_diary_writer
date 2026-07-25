from datetime import datetime

def should_generate(now: datetime, last_active: datetime | None, minutes: int) -> bool:
    return last_active is not None and (now - last_active).total_seconds() >= minutes * 60


def should_run_regular_check(now: datetime, start_delay_minutes: int = 30) -> bool:
    return now.hour != 0 or now.minute >= start_delay_minutes

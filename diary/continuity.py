from __future__ import annotations

from .models import ContinuityState, DiaryMetadata


def update_continuity(previous: ContinuityState, metadata: DiaryMetadata) -> ContinuityState:
    event_summaries = [event.summary for event in metadata.events if event.summary]
    return ContinuityState(
        previous_summary="；".join(event_summaries[:3])[:800],
        important_events=(event_summaries + previous.important_events)[:12],
        ongoing_projects=list(dict.fromkeys((metadata.projects + previous.ongoing_projects)))[:20],
        ongoing_topics=list(dict.fromkeys((metadata.ongoing_topics + metadata.topics + previous.ongoing_topics)))[:30],
        unresolved_items=list(dict.fromkeys((metadata.unresolved + previous.unresolved_items)))[:20],
        recent_changes=(event_summaries + previous.recent_changes)[:20],
    )

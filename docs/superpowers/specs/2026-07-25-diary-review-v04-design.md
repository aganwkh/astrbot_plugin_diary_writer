# v0.4 Long-Term Review and Retrieval Design

## Goal

Reuse v0.3 daily JSON as the only source of historical facts. Add weekly/monthly summaries, local diary retrieval, On This Day, and project/topic timelines without SQLite, FTS, a service process, or changes to daily facts.

## Persisted review data

Each summary is an independent Markdown/JSON pair:

```text
reviews/
  weekly/2026-W30.md
  monthly/2026-07.md
review_metadata/
  weekly/2026-W30.json
  monthly/2026-07.json
review_backups/<kind>/<period>/<timestamp>/*
review_generation_state.json
```

JSON contains `kind`, `period`, `start_date`, `end_date`, `title`, `covered_dates`, `missing_dates`, `source_diary_dates`, `summary_stale`, `stale_reason`, `stale_since`, `events`, `topics`, `people`, `projects`, `highlights`, `unresolved`, `generated_at`, `provider`, `model`, and `prompt_version`. Every field is rebuildable from daily JSON and the period definition.

Weeks are Monday–Sunday. A daily file is considered covered only when its metadata JSON is readable. Missing calendar dates remain explicit rather than blocking a summary.

## Summary lifecycle

1. After a daily MD/JSON pair is durably written, evaluate the daily date's completed weekly/monthly period. Sunday writes can create the prior ISO week; a month-end write can create the prior calendar month.
2. The automatic nightly path also runs a catch-up scan for ended periods with at least one daily metadata file and no summary.
3. Existing formal summaries are never regenerated automatically. `generate(..., force=False)` is idempotent; a manual rewrite uses `force=True` and first creates a timestamped pair backup.
4. A new, rewritten, migrated, or core-metadata-changed daily marks every already-created containing summary stale. Core fields are events, topics, people, projects, highlights, mood/mood_score, unresolved, ongoing_topics, title, and source evidence. Technical fields such as generated time, provider, model, or prompt version do not mark it stale.
5. Review generation errors update only review generation state. Daily persistence and continuity stay complete regardless.

## Retrieval and commands

`diary/retrieval.py` scans JSON metadata deterministically and returns dated reference records. It filters dates and matches normalized query terms against title, topics, people, projects, event summaries/facts, highlights, unresolved items, and tags. This file is the future vector-search replacement seam; no interface or index is persisted in v0.4.

- `/问日记 <问题>`: retrieve first. No results means a fixed no-result response. v0.4 returns verified dated local references directly; LLM synthesis is deferred until claim-level verification exists.
- `/那年今日`: return only same month/day entries from years earlier than today, with title and deterministic short summary.
- `/日记项目 <名称>` and `/日记话题 <名称>`: return first date, latest progress, and date-sorted event summaries.
- `/补写周记 <YYYY-Www>` / `/重写周记 <YYYY-Www>` and monthly counterparts call the review service.

Every new command is sensitive: authorization plus private-message gating is mandatory.

## Summary generation

The review service only passes compact daily metadata/event material to the provider. The prompt requires JSON with Markdown and all references as daily dates; the parser removes any date not in `source_diary_dates`. It does not read LivingMemory or daily Markdown, and it labels inference separately from factual material.

## Testing

Temporary daily JSON fixtures cover ISO-week/month boundaries, missing days, idempotence, backups, stale marking, old v0.3 metadata compatibility, source-date validation, retrieval misses, cross-year On This Day, and timeline ordering. Offline command tests verify private-only access.

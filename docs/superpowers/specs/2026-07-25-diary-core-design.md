# Long-Term AI Diary Core Design

## Goal

Upgrade the plugin from a direct `LivingMemory -> LLM -> Markdown` script into a private, recoverable diary pipeline. Each diary has human-readable Markdown and a structured JSON record with source evidence; all personal identity and deployment paths stay out of code and repository defaults.

## Compatibility and configuration

AstrBot remains the host. `main.py` is limited to registration, commands, message activity tracking, and cron scheduling. It gets the data root through AstrBot's `get_astrbot_data_path()` and keeps large files in `plugin_data/<plugin name>/`.

`_conf_schema.json` is the sole configuration definition. `metadata.yaml` and `@register` use the same version. Repository-local AstrBot instance configuration is removed. Defaults contain no user IDs, credentials, server paths, or enabled website destination.

The default prompt preset is `chihaya_anon`: it preserves the former first-person, warm and lightly teasing diary style, but character name, user nickname, and voice are editable. A generic factual preset is also supplied. The selected preset is copied into the effective prompt, never inferred from hard-coded identity.

## Modules

| Module | Responsibility |
|---|---|
| `diary/config.py` | Typed, validated configuration access and preset resolution. |
| `diary/models.py` | Dataclasses for memories, events, metadata, continuity and generation state. |
| `diary/storage.py` | Directory layout, JSON/Markdown atomic writes, timestamped rewrite backups, read helpers. |
| `diary/memory_source.py` | `MemorySource` protocol and a read-only SQLite LivingMemory adapter. |
| `diary/events.py` | Deterministic date ordering, text/topic deduplication and event clustering. |
| `diary/continuity.py` | Compact rolling state; no historical full diary injection. |
| `diary/prompts.py` | Prompt construction and strict JSON response parsing. |
| `diary/service.py` | Orchestrates extraction, LLM retries, validation, persistence, state transitions and sync. |
| `diary/website_sync.py` | Optional, isolated path-based mirror and index update. |
| `diary/permissions.py` | Authorization and private-message checks using AstrBot message type. |

## Files and state

```text
plugin_data/astrbot_plugin_diary_writer/
  diaries/2026-07-25.md
  metadata/2026-07-25.json
  backups/2026-07-25/20260725T010203Z.md
  backups/2026-07-25/20260725T010203Z.json
  continuity.json
  generation_state.json
```

`metadata/<date>.json` contains the requested date, title, mood and score, topics/tags, people, projects, events, highlights, unresolved and ongoing topics, all source IDs, source count, generation time, provider/model, prompt version, and an event list. Each event contains `summary`, `kind`, `facts`, `inferences`, `memory_ids`, and `time_range`.

`generation_state.json` records one pending date, stage, retry count, last error, updated time, and last successful time. A failed run leaves the previous diary untouched and makes recovery observable. A subsequent cron/manual run retries the pending date before creating a new date.

## Evidence and generation flow

1. The adapter returns source records for the requested day without changing LivingMemory.
2. The event extractor normalizes whitespace, removes duplicate source text, groups adjacent records with shared topics or high token overlap, and retains every contributing memory ID.
3. The service supplies only those events plus a compact continuity summary to the LLM. The prompt forbids adding unsupported facts and requires uncertain material to be labelled as inference.
4. The model returns a JSON envelope containing `markdown` and metadata. The service rejects malformed envelopes, removes unknown source IDs, and derives safe fallbacks for missing optional fields.
5. Storage atomically replaces both final files only after validated output is available. It writes each temporary sibling file, flushes it, then calls `replace`; a rewrite first copies the current pair into a timestamped backup directory.
6. Continuity and generation state are updated only after the diary pair is durable. Website sync is best effort and cannot roll back or prevent a local diary.

## Privacy and command policy

Every command checks configured ownership. `查看日记`, `测试日记`, `补写日记`, and `重写日记` additionally require `PRIVATE_MESSAGE`; a group invocation only receives a fixed non-sensitive reminder. `日记状态` is the only default group command and reports aggregate state without excerpts, source text, names, or pending error detail. The message listener records activity only for configured owners. Empty ownership means the plugin is inert until configured.

## Reliability rules

- `auto_write_enabled=false` prevents cron-job registration and cron generation.
- Provider selection uses a configured provider ID when present, otherwise an eligible owner private UMO; no fabricated user ID is used.
- Provider calls receive bounded retry with exponential delays and keep the final error in generation state.
- SQLite reads are read-only and fail closed when the required legacy `documents` columns are absent. This is deliberately isolated because LivingMemory has no documented day-query API.
- Website synchronization defaults to disabled and requires an explicit writable destination.

## Testing

Unit tests use temporary directories and fake sources/providers. They cover private-command protection, no-op auto writing, event evidence retention, JSON validation, atomic replacement, rewrite backups, generation-state recovery, website switch behavior, and a LivingMemory schema mismatch. A lightweight AstrBot stub makes the plugin entry point importable without a server installation.

## Out of scope

Week/month digests, Ask Diary, On This Day, charts, and WebUI are intentionally deferred. The metadata/event/continuity formats leave room for them without introducing their runtime or UI now.

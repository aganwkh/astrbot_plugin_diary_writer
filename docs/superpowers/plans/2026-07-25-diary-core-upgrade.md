# Long-Term AI Diary Core Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a private, evidence-backed, recoverable long-term diary plugin without deployment-specific defaults.

**Architecture:** Keep AstrBot handlers thin and move persistence, source access, extraction, prompt construction, generation orchestration, and website mirroring into focused standard-library modules. Save each diary as an atomic Markdown/JSON pair and use compact continuity plus generation state for recovery.

**Tech Stack:** Python 3.10+, AstrBot API, SQLite read-only access, dataclasses, `unittest`, standard library only.

---

## File structure

- `main.py`: AstrBot entry point, cron and command adapters.
- `diary/*.py`: focused core services described in the design document.
- `_conf_schema.json`, `metadata.yaml`, `README.md`: public configuration and documentation.
- `tests/test_*.py`: isolated unit tests with fakes.
- `.gitignore`: blocks host configuration, generated data and bytecode.

### Task 1: Remove host-specific repository data and establish public configuration

**Files:**
- Create: `.gitignore`
- Modify: `_conf_schema.json`, `metadata.yaml`, `README.md`
- Delete obsolete local configuration, templates, backups, and bytecode artifacts.

- [ ] Write a configuration-schema test asserting empty owner defaults, disabled website sync, preset keys, and a single public version.
- [ ] Remove instance configuration, credentials, server-specific templates, backup source, and bytecode from tracked files.
- [ ] Replace the schema with owner, automation, provider, preset, source, and website-sync settings; set `owner_ids` default to `[]` and website sync to `false`.
- [ ] Update README with private command policy, migration/configuration requirements, storage format, and secret-history warning.
- [ ] Run `python -m json.tool _conf_schema.json` and `git grep` for credential/path/user-ID markers.

### Task 2: Define models, configuration, and privacy primitives

**Files:**
- Create: `diary/__init__.py`, `diary/models.py`, `diary/config.py`, `diary/permissions.py`, `tests/test_config_and_permissions.py`

- [ ] Write failing tests for empty-owner denial, AstrBot private event acceptance, group denial, preset resolution, and disabled automation.
- [ ] Add dataclasses for source memories, events, diary metadata, continuity, and generation state.
- [ ] Implement configuration normalization so list/string inputs are safe and `chihaya_anon` is a named preset rather than an identity embedded in program logic.
- [ ] Implement a single permission gate returning only a fixed safe group reminder.
- [ ] Run `python -m unittest tests.test_config_and_permissions -v`.

### Task 3: Implement durable storage and recovery state

**Files:**
- Create: `diary/storage.py`, `tests/test_storage.py`

- [ ] Write failing tests that prepopulate a diary, simulate a rewrite, and assert both timestamped backups exist; assert a failed temporary write cannot replace a final file.
- [ ] Implement `atomic_write_text(path, content)` using a same-directory temporary filename, flush/fsync, and `Path.replace`.
- [ ] Implement paired diary writes, metadata/state/continuity reads, and timestamped MD/JSON rewrite backups.
- [ ] Run `python -m unittest tests.test_storage -v`.

### Task 4: Isolate LivingMemory access and event extraction

**Files:**
- Create: `diary/memory_source.py`, `diary/events.py`, `tests/test_events.py`, `tests/test_memory_source.py`

- [ ] Write failing tests for read-only source records, schema mismatch handling, duplicate collapse, topic/time grouping, and every cluster retaining all memory IDs.
- [ ] Define a `MemorySource` protocol; implement `SQLiteLivingMemorySource` using a configured database path and `mode=ro` connection.
- [ ] Validate the legacy `documents(id,text,metadata)` contract in the adapter only; return an empty result plus diagnostic exception on incompatibility.
- [ ] Implement deterministic token-overlap/topic clustering without dependencies.
- [ ] Run `python -m unittest tests.test_events tests.test_memory_source -v`.

### Task 5: Build prompt, continuity, and validated LLM envelope processing

**Files:**
- Create: `diary/prompts.py`, `diary/continuity.py`, `tests/test_prompts.py`, `tests/test_continuity.py`

- [ ] Write failing tests for a prompt that includes only event evidence/continuity, rejects invented IDs, labels inference, and preserves the selected preset.
- [ ] Construct a versioned JSON-envelope instruction with generic factual constraints and configurable persona fields.
- [ ] Parse response JSON, normalize optional metadata, reject unknown memory IDs, and produce Markdown plus structured metadata.
- [ ] Implement a bounded continuity reducer that stores only summaries, ongoing topics/projects, unresolved items, and recent changes.
- [ ] Run `python -m unittest tests.test_prompts tests.test_continuity -v`.

### Task 6: Orchestrate generation, retries, state, and optional website sync

**Files:**
- Create: `diary/service.py`, `diary/website_sync.py`, `tests/test_service.py`

- [ ] Write failing tests for provider retry, failed-state persistence, successful-state cleanup, local persistence despite sync failure, and disabled sync creating no destination files.
- [ ] Implement `DiaryService.generate(date, force=False)` to set pending state, extract event evidence, call the provider with bounded retry, write the diary pair, update continuity/state, then invoke best-effort sync.
- [ ] Use configured provider ID where AstrBot supports lookup; otherwise use the invoking private event UMO. Cron generation must skip when no valid owner UMO is available.
- [ ] Implement mirror/index writes with the same atomic storage primitive and only when explicitly enabled.
- [ ] Run `python -m unittest tests.test_service -v`.

### Task 7: Replace the plugin entry point with thin secure adapters

**Files:**
- Modify: `main.py`
- Create: `tests/astrbot_stubs.py`, `tests/test_plugin_commands.py`

- [ ] Write failing command tests for group denial of every sensitive command, status aggregate redaction, no cron registration when automation is disabled, and rewrite backup behavior.
- [ ] Wire configuration, storage, source, service, and permissions in `DiaryWriterPlugin.initialize`.
- [ ] Register cron jobs only when `auto_write_enabled` and at least one owner are configured; track only owner activity.
- [ ] Route view/test/backfill/rewrite through the private permission gate; preserve only safe status reporting in an explicitly configured group whitelist.
- [ ] Run `python -m unittest tests.test_plugin_commands -v`.

### Task 8: Verify packaging, safety, and regression behavior

**Files:**
- Modify: `README.md`, `docs/superpowers/specs/2026-07-25-diary-core-design.md`, `docs/superpowers/plans/2026-07-25-diary-core-upgrade.md`

- [ ] Run the complete suite: `python -m unittest discover -s tests -v`.
- [ ] Run syntax compilation: `python -m compileall -q main.py diary tests`.
- [ ] Validate JSON/YAML-facing files and search tracked content/history for user IDs, credentials, instance paths, and website paths.
- [ ] Inspect `git diff --check`, `git status`, `git diff --stat`, and full `git diff`.
- [ ] Do not commit or push; the user explicitly requires diff review first.

## Plan self-review

- P0 privacy, configuration, deployment path, versioning, website isolation, and sensitive-file cleanup are covered by Tasks 1, 2, and 7.
- Metadata, source evidence, event clustering, factual prompting, continuity, state, atomic writes, retries, and backups are covered by Tasks 3–6.
- Tests and requested final Git evidence are covered by Task 8.
- No large deferred feature is scheduled; the design reserves only the persisted fields needed for later work.

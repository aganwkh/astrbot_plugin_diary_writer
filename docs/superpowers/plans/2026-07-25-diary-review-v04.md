# v0.4 Long-Term Review and Retrieval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement task-by-task.

**Goal:** Add rebuildable weekly/monthly review and private local historical retrieval based only on daily metadata.

**Architecture:** Extend existing storage with review pair paths/backups and add compact review/retrieval modules. Daily generation remains the authority; a best-effort post-write hook and nightly catch-up invoke review work after daily success.

**Tech Stack:** Python 3.10+, dataclasses, standard library, AstrBot adapters, unittest.

---

### Task 1: Review models and durable storage

**Files:** `diary/models.py`, `diary/storage.py`, `tests/test_review_storage.py`

- [ ] Add review metadata/reference dataclasses with source dates, coverage, stale state, and factual/inference event fields.
- [ ] Add review MD/JSON paths, atomic pair writes, per-period backups, metadata loading/listing, and review generation state.
- [ ] Test weekly/monthly paths, pair rollback, rewrite backups, and v0.3 daily metadata loading.

### Task 2: Period collection, stale marking, and summary generation

**Files:** `diary/reviews.py`, `diary/review_prompts.py`, `tests/test_reviews.py`

- [ ] Implement ISO week and calendar month boundaries; collect readable daily metadata only and compute missing dates.
- [ ] Implement stale marking for core daily changes, preserving formal content and recording reason/time.
- [ ] Implement idempotent, retrying review generation from compact daily metadata; reject provider-created source dates and keep daily failure-isolated.
- [ ] Test boundaries, missing dates, duplicate generation, stale state, failed provider, and forced rewrite backup.

### Task 3: Local metadata retrieval and source-safe answers

**Files:** `diary/retrieval.py`, `diary/ask_diary.py`, `tests/test_retrieval.py`

- [ ] Scan daily metadata for normalized date/keyword/topic/person/project/event matches.
- [ ] Add Ask Diary answer composition that never calls LLM on no results and validates all answer source dates against retrieval results.
- [ ] Add On This Day and project/topic timeline queries with deterministic date ordering.
- [ ] Test no-result behavior, valid source dates, rejected fabricated dates, cross-year date matching, and first/latest timelines.

### Task 4: Wire daily lifecycle, cron catch-up, private commands, and documentation

**Files:** `diary/service.py`, `diary/permissions.py`, `main.py`, `README.md`, `_conf_schema.json`, `tests/test_offline_plugin_smoke.py`, `tests/test_review_commands.py`

- [ ] Invoke stale marking and best-effort completed-period generation after successful daily persistence without changing daily success/failure behavior.
- [ ] Run catch-up only after automatic daily flow and only for ended periods without formal summaries.
- [ ] Add private-only command adapters for weekly/monthly backfill/rewrite, Ask Diary, On This Day, project, and topic lookups.
- [ ] Document period format, stale summaries, and source-backed answers; update version to 0.4.0.
- [ ] Test group and unauthorized denial for every new command.

### Task 5: Full offline verification

**Files:** all touched files

- [ ] Run `python -m unittest discover -s tests -v`.
- [ ] Run `python -m compileall -q main.py diary tests`.
- [ ] Run `git diff --check`, `git status`, `git diff --stat`, and `git diff`.
- [ ] Do not connect to production, commit, or push.

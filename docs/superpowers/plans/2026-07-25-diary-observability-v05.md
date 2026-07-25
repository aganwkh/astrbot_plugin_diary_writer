# v0.5 Long-Term Observability and Plugin Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver yearly reviews, read-only descriptive trends, opt-in safe On This Day reminders, and a zero-build AstrBot management page.

**Architecture:** Extend existing review/storage/permission services instead of adding data stores. One native Plugin Page calls narrowly validated plugin-local APIs; daily JSON remains the factual authority and reviews/trends remain rebuildable derivatives.

**Tech Stack:** Python standard library, AstrBot Plugin Pages bridge and `astrbot.api.web`, native HTML/CSS/JavaScript, SVG, unittest.

---

## File map

- Modify `diary/reviews.py`, `diary/storage.py`, `diary/config.py`, `diary/models.py`, `main.py`, `_conf_schema.json`, `README.md`, and `metadata.yaml`.
- Create `diary/trends.py`, `diary/web_api.py`, `pages/diary-manager/{index.html,styles.css,api.js,state.js,render.js,charts.js,app.js}`, `.astrbot-plugin/i18n/zh-CN.json`, and v0.5 tests.
- Reuse `DiaryService`, `ReviewService`, `DiaryStorage`, retrieval helpers, generation state, atomic pair writes, and existing locks.

### Task 1: Add tests for calendar-year review behavior

**Files:** Modify `tests/test_v04_reviews.py`; create `tests/test_v05_yearly.py`.

- [ ] Add a failing test that `period_dates("yearly", "2024")` has 366 entries and `period_dates("yearly", "2025")` has 365 entries.
- [ ] Add a failing test that one daily JSON plus one monthly review creates a yearly review with month coverage and daily-only event totals.
- [ ] Extend `ReviewService` with `yearly` period parsing, source coverage, monthly context, direct daily source fingerprints, and existing pair-write/retry behavior.
- [ ] Run `python -m unittest tests.test_v05_yearly -v` and expect all yearly tests to pass.

### Task 2: Propagate stale state without rewriting summaries

**Files:** Modify `diary/reviews.py`; extend `tests/test_v05_yearly.py`.

- [ ] Add failing tests for a core daily change marking yearly stale, a technical daily field not marking yearly stale, and a rewritten referenced monthly review marking yearly stale.
- [ ] Add a review-to-review stale marker that updates JSON only and records source kind, period, reason, and timestamp.
- [ ] Invoke the marker after successful forced monthly generation; keep automatic idempotence and formal Markdown unchanged.
- [ ] Run `python -m unittest tests.test_v05_yearly -v`.

### Task 3: Compute read-only trends

**Files:** Create `diary/trends.py`; create `tests/test_v05_trends.py`.

- [ ] Add failing fixtures covering mood scores, duplicate/case-varied topics/projects, absent fields, recorded events, unresolved lists, and no metadata.
- [ ] Implement `build_trends(storage, start, end)` with date validation, monthly buckets, daily-only frequencies, and project observed/added/absent changes.
- [ ] Ensure the function performs no writes and treats invalid/missing optional metadata as empty.
- [ ] Run `python -m unittest tests.test_v05_trends -v`.

### Task 4: Add opt-in, non-command On This Day reminder

**Files:** Modify `diary/config.py`, `diary/storage.py`, `diary/permissions.py`, `main.py`, `_conf_schema.json`; create `tests/test_v05_reminders.py`.

- [ ] Add failing tests for disabled reminder, private authorized ordinary message, command suppression, group suppression, no historical entry, and same-day idempotence.
- [ ] Add `on_this_day_reminder_enabled` defaulting false plus atomic reminder-state storage.
- [ ] Add a private/authorized non-command helper returning only real `on_this_day` references; let the listener yield it without `stop_event`.
- [ ] Run `python -m unittest tests.test_v05_reminders -v`.

### Task 5: Add authenticated Plugin Page API adapters

**Files:** Create `diary/web_api.py`; modify `main.py`; create `tests/test_v05_web_api.py`.

- [ ] Add failing handler tests for missing Dashboard username, invalid kind/period/range/limit, absent configured provider on generate, and read-only endpoint responses.
- [ ] Implement `DiaryWebAPI` methods for overview, calendar, entry, search, entities, timeline, trends, and generate using existing services.
- [ ] Register `/{plugin_name}/...` routes through `context.register_web_api`; import response/request helpers from `astrbot.api.web` only when AstrBot provides them.
- [ ] Limit calendar windows and result counts; return no static or cached private data.
- [ ] Run `python -m unittest tests.test_v05_web_api -v`.

### Task 6: Build the zero-build responsive Page

**Files:** Create all `pages/diary-manager/*` assets and `.astrbot-plugin/i18n/zh-CN.json`; create `tests/test_v05_page_assets.py`.

- [ ] Add static tests asserting bridge initialization, separate assets, theme variables, mobile breakpoint, no `innerHTML`, no LocalStorage, and no diary fixture text in assets.
- [ ] Implement plain DOM rendering with `textContent`, memory-only state, parameterized bridge calls, responsive CSS, and SVG charts.
- [ ] Load summary lists first and full entry/evidence only after explicit user selection.
- [ ] Run `python -m unittest tests.test_v05_page_assets -v`.

### Task 7: Wire commands, documentation, and final verification

**Files:** Modify `main.py`, `README.md`, `metadata.yaml`, `_conf_schema.json`, docs; extend offline smoke tests.

- [ ] Add private-only yearly backfill/rewrite commands and include them in sensitive-command coverage.
- [ ] Document provider requirement for Page generation, Page privacy constraints, yearly stale behavior, and reminder default.
- [ ] Set all public version declarations to `0.5.0`.
- [ ] Run `python -m unittest discover -s tests -v`, `python -m compileall -q main.py diary tests`, and `git diff --check`.
- [ ] Inspect `git status`, `git diff --stat`, and `git diff`; do not commit or push.

## Self-review

- Annual review, coverage, atomic rewrite, stale propagation, trend statistics, reminder gating, Plugin Pages bridge/API, DOM safety, v0.4 compatibility, and offline tests each have a dedicated task.
- No task adds a database, framework, service, cache, production connection, or a second factual store.
- All Page text is handled as untrusted data and no task renders generated Markdown as HTML.

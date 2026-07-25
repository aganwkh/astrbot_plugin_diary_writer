# v0.6 Fact Corrections, Archives, and Integrity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add deterministic fact correction, versioned recovery, safe archives, lifecycle, subjective reflection, and integrity audit without creating another factual database.

**Architecture:** Reuse `DiaryStorage`, `ReviewService`, retrieval, and the authenticated Plugin Page API. Current daily JSON holds corrected facts; revisions, corrections, archives, lifecycle, reflections, and audits are append-only or read-only derived data.

**Tech Stack:** Python standard library, AstrBot Plugin Pages bridge/API, native HTML/CSS/JavaScript, unittest.

---

## File map

- Create `diary/maintenance.py`, `diary/corrections.py`, `diary/archives.py`, `diary/lifecycle.py`, `diary/reflections.py`, `diary/integrity.py` and focused `tests/test_v06_*.py`.
- Modify `diary/storage.py`, `diary/models.py`, `diary/reviews.py`, `diary/retrieval.py`, `diary/trends.py`, `diary/web_api.py`, `main.py`, Page assets, documentation, version declarations, and offline AstrBot stubs.

### Task 1: Stable identifiers, revision chain, and deterministic correction

**Files:** Create `diary/corrections.py`, `tests/test_v06_corrections.py`; modify `diary/storage.py`, `diary/models.py`, `diary/reviews.py`, `diary/retrieval.py`.

- [x] Write failing tests for one-match field/fact edits, zero/multiple-match rejection, no unrelated metadata mutation, exact Markdown replacement/note fallback, stable event/fact IDs, revision parent/current links, supersession, rollback, and stale propagation.
- [x] Add `ensure_stable_ids(metadata)` that preserves legacy facts while adding persistent event/fact IDs without treating IDs as factual changes.
- [x] Implement correction application and rollback under a date lock: snapshot first, atomically persist daily pair and correction/revision chain, and mark daily-derived reviews/reflections stale.
- [x] Run `python -m unittest tests.test_v06_corrections -v`.

### Task 2: Global maintenance gate and safe archives

**Files:** Create `diary/maintenance.py`, `diary/archives.py`, `tests/test_v06_archives.py`; modify `diary/storage.py`, `diary/service.py`, `diary/reviews.py`, `main.py`.

- [x] Write failing tests for manifest checksums, filtered settings, rejected traversal/drive/backslash/symlink/ratio/size/count inputs, dry run, pre-restore archive, failed restore rollback, and concurrent mutation blocked by restore.
- [x] Implement a shared async maintenance gate and route daily/review/correction/reflection mutations through it.
- [x] Implement ZIP export, verify, staged restore, five-snapshot retention, and a nonrecursive archive whitelist.
- [x] Run `python -m unittest tests.test_v06_archives -v`.

### Task 3: Lifecycle, reflection, and integrity audit

**Files:** Create `diary/lifecycle.py`, `diary/reflections.py`, `diary/integrity.py`, `tests/test_v06_derived.py`.

- [x] Write failing fixtures for cross-month lifecycle, long absence/reappearance, corrected fact visibility, reflection fact separation/stable refs/staleness, and every integrity finding class.
- [x] Implement daily-only observational lifecycle summaries.
- [x] Implement explicitly requested subjective reflection generation with source refs and no daily mutation.
- [x] Implement check-only audit plus safe repair limited to deterministic compatibility fields and stale refresh.
- [x] Run `python -m unittest tests.test_v06_derived -v`.

### Task 4: Commands, authenticated Web API, and Page controls

**Files:** Modify `main.py`, `diary/web_api.py`, `pages/diary-manager/{api.js,app.js,render.js,index.html,styles.css}`, `tests/test_v06_web.py`.

- [x] Write failing tests for private correction command, Dashboard identity gate, illegal target/restore payload rejection, hidden archive paths/errors, and text-node-only rendering.
- [x] Add correction/rollback, archive verify/export/restore, lifecycle, reflection, and integrity endpoints with bounded validated parameters.
- [x] Add Page controls that fetch sensitive data only after a click and render it with `textContent`.
- [x] Run `python -m unittest tests.test_v06_web -v`.

### Task 5: Compatibility, documentation, and release verification

**Files:** Modify `README.md`, `_conf_schema.json`, `metadata.yaml`, docs, and existing smoke tests.

- [x] Verify v0.3-v0.5 fixtures load without fact inference and can receive deterministic IDs/corrections/archive export.
- [x] Document correction syntax, archive safety/retention, reflection boundary, audit safe-repair limits, and production validation scope; set version to `0.6.0`.
- [x] Run `python -m unittest discover -s tests -v`, `python -m compileall -q main.py diary tests`, and `git diff --check`.
- [x] Inspect `git status`, `git diff --stat`, and `git diff`; do not commit or push.

## Self-review

- Stable IDs, version chain, correction priority, stale propagation, archive safety, maintenance exclusion, lifecycle, reflection separation, integrity restrictions, WebUI security, compatibility, and required tests each map to a task.
- The plan adds no database, LLM correction path, frontend framework, production connection, or Git history operation.

# Disable Continuity and Simplify Activity Modes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent `continuity.json` from being included in new diary prompts and classify low activity solely by same-day LivingMemory count.

**Architecture:** Keep persistence of the legacy continuity state unchanged, but replace the loaded state with an empty `ContinuityState` only at prompt construction. Replace the three-mode classifier with a two-mode memory-count boundary: 0–4 same-day memories is `low_activity`; 5 or more is `normal`. User-requested rewrites migrate legacy `sparse` entries to that boundary while preserving their saved chat sources.

**Tech Stack:** Python 3.10, `unittest`, AstrBot plugin service.

---

### Task 1: Stop injecting continuity into prompt construction

**Files:**
- Modify: `diary/service.py:95-113,158-160`
- Modify: `tests/test_prompts_and_service.py`

- [x] **Step 1: Write the failing test**

```python
async def test_generation_omits_saved_continuity_from_provider_prompt(self):
    storage.save_continuity(ContinuityState(unresolved_items=["old server issue"]))
    await DiaryService(config, storage, source).generate(day, provider)
    self.assertNotIn("old server issue", provider.prompt)
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_prompts_and_service.ServiceTests.test_generation_omits_saved_continuity_from_provider_prompt -q`

Expected: FAIL because the current prompt serializes `storage.load_continuity()`.

- [x] **Step 3: Write minimal implementation**

```python
empty_continuity = ContinuityState()
system, prompt = build_adaptive_messages(..., empty_continuity, ...)
```

Use `ContinuityState()` for every prompt-builder call in `generate` and `preview`; do not change storage reads/writes, prompt text, or source selection.

- [x] **Step 4: Run tests to verify it passes**

Run: `python -m unittest tests.test_prompts_and_service tests.test_v11_activity -q`

Expected: PASS.

- [x] **Step 5: Run final verification and deploy**

Run: `python -m unittest discover -s tests -q`, `python -m compileall -q diary main.py`, and `git diff --check`.

Expected: only the known Windows `0644` versus `0666` permission assertion may fail. Copy only `diary/service.py` to `/opt/AstrBot/data/plugins/astrbot_plugin_diary_writer/diary/service.py`, restart `astrbot`, and verify it is active.

### Task 2: Classify activity from LivingMemory only

**Files:**
- Modify: `diary/activity.py`
- Modify: `diary/service.py`
- Modify: `tests/test_v11_activity.py`

- [x] **Step 1: Write the failing tests**

```python
self.assertEqual(classify_entry_type(0), "low_activity")
self.assertEqual(classify_entry_type(4), "low_activity")
self.assertEqual(classify_entry_type(5), "normal")
```

Add an integration assertion that a day with three activity rounds and zero same-day memory entries is `low_activity`.

- [x] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_v11_activity.ActivityTrackerTests.test_entry_type_uses_livingmemory_count_only -q`

Expected: FAIL because the current classifier returns `sparse` when the round count exceeds two.

- [x] **Step 3: Write minimal implementation**

```python
def classify_entry_type(memory_count: int) -> str:
    return "low_activity" if max(0, int(memory_count)) <= 4 else "normal"
```

Pass only `len(memories)` from `DiaryService.generate` and retain the existing 3–5 historical-memory path for `low_activity`.

- [x] **Step 4: Run tests, deploy, and verify**

Run: `python -m unittest tests.test_v11_activity tests.test_prompts_and_service -q`, `python -m unittest discover -s tests -q`, `python -m compileall -q diary main.py`, and `git diff --check`.

Copy only changed runtime files to the server, restart `astrbot`, and verify that it is active.

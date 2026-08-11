# Historical Memory Temporal Envelope Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every recent or historical memory in adaptive provider prompts an explicit past-time anchor relative to the target diary date, then deploy the verified prompt change to `codex-server`.

**Architecture:** Keep saved metadata and `DiaryService` snapshots unchanged. Add one prompt-local formatter in `diary/prompts.py` that converts raw context snapshots into temporal envelopes immediately before JSON serialization; tests exercise this prompt seam and the existing service path. Deploy only the changed runtime prompt module after a server-side backup.

**Tech Stack:** Python 3.10, `unittest`, JSON prompt payloads, PowerShell/OpenSSH, systemd.

---

### Task 1: Specify the prompt envelope with failing tests

**Files:**
- Modify: `tests/test_prompts_and_service.py`
- Modify: `tests/test_v11_activity.py`

- [ ] **Step 1: Update the low-activity prompt test with valid dated sources**

Use a target date of `2026-07-25` and source timestamps `2026-07-23T10:00:00+08:00` and `2026-06-01T09:00:00+08:00`. Assert that each serialized source contains:

```python
{
    "memory_id": "historical-1",
    "source_role": "past_context_only",
    "temporal_context": {
        "anchor_date": "2026-06-01",
        "recorded_at": "2026-06-01T09:00:00+08:00",
        "date_basis": "livingmemory_record_timestamp",
        "relation_to_diary": "past",
        "days_before_diary": 54,
        "same_day_as_diary": False,
    },
    "text": "历史回忆",
}
```

Also assert that the system contract says relative expressions in `text` use `temporal_context.anchor_date`, not the diary date.

- [ ] **Step 2: Add malformed and non-past source coverage**

Pass one missing timestamp, one invalid timestamp, and one timestamp equal to the diary date. Assert that none appears in the serialized context list and none causes `required_usage` to demand a missing candidate.

- [ ] **Step 3: Extend the service-level low-activity test**

Capture the provider prompt and assert it contains the temporal envelope while `storage.load_metadata(date)["historical_memory_sources"]` remains the original raw snapshot with `occurred_at` and without `temporal_context`.

- [ ] **Step 4: Run tests and verify RED**

Run:

```powershell
python -m unittest tests.test_prompts_and_service.PromptTests.test_low_activity_contract_allows_recent_and_frozen_history tests.test_prompts_and_service.PromptTests.test_invalid_or_non_past_context_sources_are_omitted tests.test_v11_activity.ActivityTrackerTests.test_low_activity_uses_history_when_livingmemory_is_sparse_and_normal_keeps_normal_prompt_path
```

Expected: failures showing raw context sources have no `source_role` or `temporal_context`, and malformed sources are still serialized.

### Task 2: Implement the prompt-local temporal envelope

**Files:**
- Modify: `diary/prompts.py`

- [ ] **Step 1: Bump the prompt version and strengthen the contract**

Set:

```python
UNIFIED_PROMPT_VERSION = "v1.1.3-temporal-envelope"
```

Add one contract rule stating that each context source is `past_context_only` and that relative expressions in its `text` must be interpreted against `temporal_context.anchor_date`, never the diary date.

- [ ] **Step 2: Add the minimal formatter**

Import `date` and add one private function:

```python
def _context_sources(diary_date: str, sources: list[dict]) -> list[dict[str, Any]]:
    target = date.fromisoformat(diary_date)
    result = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        recorded_at = str(source.get("occurred_at") or "").strip()
        try:
            anchor = datetime.fromisoformat(recorded_at.replace("Z", "+00:00")).date()
        except ValueError:
            continue
        days_before = (target - anchor).days
        if days_before <= 0:
            continue
        remaining = {key: value for key, value in source.items() if key not in {"memory_id", "occurred_at", "text"}}
        result.append({
            "memory_id": str(source.get("memory_id") or ""),
            "source_role": "past_context_only",
            "temporal_context": {
                "anchor_date": anchor.isoformat(),
                "recorded_at": recorded_at,
                "date_basis": "livingmemory_record_timestamp",
                "relation_to_diary": "past",
                "days_before_diary": days_before,
                "same_day_as_diary": False,
            },
            "text": str(source.get("text") or ""),
            **remaining,
        })
    return result
```

- [ ] **Step 3: Serialize envelopes without changing storage snapshots**

In `_material`, derive prompt-only recent and historical lists with `_context_sources`. Pass those derived lists to `_mode_contract`, serialize them into the payload, and leave the caller-owned raw lists untouched.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the exact command from Task 1 Step 4. Expected: `Ran 3 tests` and `OK`.

- [ ] **Step 5: Run the adjacent regression tests**

Run:

```powershell
python -m unittest tests.test_prompts_and_service tests.test_v11_activity
```

Expected: all tests pass with `OK`.

- [ ] **Step 6: Commit the implementation**

Stage only the spec correction, plan, prompt module, and two test files. Commit with:

```powershell
git commit -m "fix: anchor historical memories to source dates"
```

### Task 3: Verify and deploy to the confirmed server

**Files:**
- Deploy: `diary/prompts.py` to `/opt/astrbot/data/plugins/astrbot_plugin_diary_writer/diary/prompts.py`

- [ ] **Step 1: Run full local verification**

Run:

```powershell
python -m unittest discover -s tests
python -m compileall -q .
git diff --check
```

Expected: the full suite reports `OK`; compile and diff checks exit zero.

- [ ] **Step 2: Create a recoverable server-side backup**

Over SSH alias `codex-server`, create a timestamped archive beside the plugin directory and verify it is non-empty before replacing any runtime file:

```bash
diary_backup_path="/opt/astrbot/data/plugins/astrbot_plugin_diary_writer-before-temporal-envelope-$(date +%Y%m%dT%H%M%S).tar.gz"
tar -czf "$diary_backup_path" -C /opt/astrbot/data/plugins astrbot_plugin_diary_writer
test -s "$diary_backup_path"
printf '%s\n' "$diary_backup_path"
```

- [ ] **Step 3: Transfer and validate the new runtime module**

Copy local `diary/prompts.py` to a temporary remote path, compile it there, compare its SHA-256 with the local file, then install it over the runtime file with mode `0644`.

- [ ] **Step 4: Restart and verify live behavior**

Restart `astrbot`, wait for delayed listener initialization, then verify:

```bash
systemctl is-active astrbot
ss -ltn | grep ':8788 '
curl -fsS -o /dev/null -w '%{http_code}' http://127.0.0.1:8788/
grep -n 'v1.1.3-temporal-envelope\|past_context_only\|anchor_date' /opt/astrbot/data/plugins/astrbot_plugin_diary_writer/diary/prompts.py
```

Expected: service `active`, listener present, HTTP `200`, and all three prompt markers present in the deployed module.

- [ ] **Step 5: Preserve rollback information**

Report the exact backup archive path, deployed SHA-256, local commit, service status, and HTTP result. Do not push unless separately requested.

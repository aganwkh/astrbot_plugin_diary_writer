# Historical memory temporal envelope

## Goal

Make the generation model understand when each recent or historical LivingMemory record belongs without forcing a particular date style in the diary body and without adding a second model call, self-check, or rewrite pass.

## Problem

Adaptive diary prompts currently send a raw `occurred_at` beside each memory while the memory's temporal role is declared separately in `mode_contract`. The model must connect those distant fields and may incorrectly interpret relative words such as “今天”“昨天”“前天” against the diary date instead of the memory's original date.

The available timestamp comes from LivingMemory record metadata. It is reliable as the record's temporal anchor, but it must not be presented as a verified event-occurrence time when the source does not prove that distinction.

## Decision

Keep the existing recent and historical source lists, memory IDs, stored snapshots, selection, cooldown, and rewrite behavior. For prompt material only, wrap every context memory in a temporal envelope derived from the target diary date and the saved `occurred_at` value.

```json
{
  "memory_id": "1403",
  "source_role": "past_context_only",
  "temporal_context": {
    "anchor_date": "2026-06-22",
    "recorded_at": "2026-06-22T22:02:09.884116",
    "date_basis": "livingmemory_record_timestamp",
    "relation_to_diary": "past",
    "days_before_diary": 49,
    "same_day_as_diary": false
  },
  "text": "昨天发生了一件事……"
}
```

The prompt contract must state that relative time expressions inside `text` are interpreted against `temporal_context.anchor_date`, never against the target diary date. `source_role: past_context_only` means the memory may provide background, recollection, association, or emotion but cannot independently establish a same-day fact.

## Field semantics

- `anchor_date`: calendar date derived from the source record timestamp; the reference date for relative expressions in that memory text.
- `recorded_at`: the original full ISO timestamp retained for traceability.
- `date_basis`: fixed value `livingmemory_record_timestamp`, preventing the timestamp from being overstated as a verified event time.
- `relation_to_diary`: fixed value `past` for recent and historical context supplied to adaptive generation.
- `days_before_diary`: non-negative calendar-day difference between `anchor_date` and the target diary date.
- `same_day_as_diary`: fixed `false` for these context lists, giving the model an immediate boolean distinction.
- `source_role`: fixed value `past_context_only`, colocated with the memory instead of requiring the model to infer its role only from `mode_contract`.

All derived values use the same local calendar-date interpretation already used by memory selection. No new claim about the underlying event date is introduced.

## Data flow

`DiaryService` continues to save and reuse the existing source snapshots. The prompt-building module receives the target diary date and transforms each context snapshot into the temporal-envelope representation immediately before JSON serialization. This keeps the format change local to the prompt seam and avoids metadata migration or rewrite incompatibility.

Normal entries remain unchanged because they do not receive recent or historical context lists. Same-day `today_events` remain the only source allowed to establish structured same-day facts.

## Failure behavior

Malformed saved context records without a valid `occurred_at` are omitted from the prompt rather than assigned a guessed date. Existing generation error handling remains unchanged. The design adds no semantic post-check, retry, second provider call, or automatic diary rewrite.

## Compatibility

- Existing metadata retains its current shape.
- Existing low-activity rewrites still reuse their saved candidate memories.
- Historical usage IDs and cooldown accounting continue to use the unchanged `memory_id` values.
- Public-site and Web API payloads remain unchanged because the envelope exists only in provider prompt material.
- Prompt version must change so generated metadata identifies the new input contract.

## Verification

- Add a prompt-level test asserting that a historical record contains the complete temporal envelope and correct day difference.
- Add a prompt-level test asserting that the contract anchors “今天”“昨天”“前天” to `anchor_date`, not the diary date.
- Add a service-level test proving existing stored snapshots remain unchanged while the captured provider prompt uses the envelope.
- Add a rewrite test proving saved low-activity sources receive the envelope without reselection.
- Keep the existing test proving recent and historical memory IDs cannot enter same-day structured events.
- Run the affected prompt/service tests and the full test suite.

## Non-goals

- Requiring the diary body to print an absolute date.
- Inferring an event date from free-form memory text.
- Adding model self-review, validation calls, or rewrite retries.
- Migrating existing diary metadata or changing LivingMemory storage.

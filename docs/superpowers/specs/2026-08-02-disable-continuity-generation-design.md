# Disable continuity and simplify activity modes

## Goal

Stop injecting the undated `continuity.json` state into new diary prompts and remove the `sparse` entry type. This prevents old unresolved items from being presented as current context and ensures days with too little LivingMemory use historical material.

## Change

- Daily generation and preview pass an empty `ContinuityState` to prompt builders.
- Keep reading, writing, and archiving `continuity.json` unchanged for compatibility; it is simply no longer a generation source.
- Classify solely by same-day LivingMemory count: 0–4 is `low_activity`; 5 or more is `normal`.
- Keep the 3–5 historical-memory selection for every `low_activity` diary.
- Keep normal and existing low_activity rewrites frozen to their saved sources. A user-requested rewrite of a legacy `sparse` diary reclassifies it, retains its saved chat sources, and selects current low_activity history when applicable; automatic generation never rewrites old diaries.
- The deployed instance's single configured `diary_main_prompt` adds a user-approved encouragement to expand otherwise quiet days; no source prompt contract changes.

## Verification

- Add a service-level test proving stored continuity is absent from the provider prompt.
- Add classification tests proving three chat rounds with zero LivingMemory entries are still `low_activity`.
- Run affected tests and the full test suite, recording the existing Windows permission assertion separately if it remains.

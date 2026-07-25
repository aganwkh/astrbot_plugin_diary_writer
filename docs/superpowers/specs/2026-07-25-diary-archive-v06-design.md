# v0.6 Fact Corrections, Archives, and Integrity Design

## Fact authority and stable references

Current `metadata/<date>.json` remains the only current factual view. User corrections atomically update that view; revisions and corrections are immutable historical records and are never read by ordinary retrieval, trends, lifecycle, or factual Ask Diary.

Each event receives a persistent `event_id`; each factual statement receives a persistent `fact_id` in `fact_records`. Legacy `facts: list[str]` remains synchronized for v0.3-v0.5 readers, while `fact_records: [{id, value}]` is authoritative for targeted correction. IDs are deterministically added once from the date, evidence and original values, then persist through value edits. Array positions are accepted only as a temporary UI locator and never written into corrections or reflections as the durable reference.

## Corrections and revision chain

Before a correction or rollback, storage creates a complete snapshot. `revision_chain/<date>.json` points at `current_revision_id`; every revision records `id`, `parent_revision_id`, `correction_id`, `created_at`, and a complete MD/JSON snapshot. A correction records stable target references, old/new values, source, revision links, and one of `active`, `superseded`, or `rolled_back`.

Only field/value operations with exactly one match are accepted. Markdown changes are exact replacements only. If a structured fact can be changed but its prose occurrence is zero or ambiguous, the operation appends a fixed correction note instead of guessing prose. No correction, rollback, or safe repair invokes an LLM. A rollback creates a new revision and correction record; it never deletes history. It marks later reverted correction records `rolled_back` and makes replacement corrections `superseded` where their stable target no longer supplies the current value.

Every successful factual mutation calls the existing review stale marker for the date and marks affected reflection records stale. Current daily metadata therefore drives corrected retrieval, timelines, trends, and lifecycle results.

## Archives and maintenance gate

`diary/archives.py` uses only `zipfile`, `hashlib`, `tempfile`, and `shutil`. Archives contain whitelisted data trees: daily, metadata, reviews, review metadata, revisions, corrections, reflections, continuity, state files, and an explicitly filtered non-instance settings snapshot. Archives and pre-restore snapshots are excluded to prevent recursive ZIP growth. A manifest lists version, creation time, paths, byte sizes, SHA-256 checksums, and compatibility range.

Verification rejects duplicate names, absolute paths, drive paths, backslashes, `..`, symlinks, non-whitelisted paths, excessive file counts, excessive total/single uncompressed size, and unsafe compression ratios. Restore first validates into a temporary directory, creates a separate pre-restore snapshot, then applies only allowed files with rollback-on-failure. It never restores credentials, owners, provider IDs, instance paths, or external sync paths. Pre-restore snapshots retain the newest five archives.

A process-wide asynchronous maintenance gate serializes every mutation. Restore takes it exclusively; cron daily generation, manual generation, corrections, rollbacks, review/reflection generation, and WebUI mutations enter the same gate, so no date-level operation can write during a restore.

## Derived lifecycle, reflection, and integrity

`diary/lifecycle.py` scans corrected daily JSON and reports observational person/project timelines only. `diary/reflections.py` stores explicit monthly/yearly subjective observations separately from facts. Reflection records use `source_refs` containing date, event/fact stable ID, and field; source fact text is a snapshot only. They are generated only on explicit request, never added to factual search or statistics, and become stale when a referenced daily fact changes.

`diary/integrity.py` reports missing pairs, malformed compatible metadata, invalid event/fact links, stale reviews/reflections, bad revision/correction chains, archive-manifest failures, and abnormal generation states. Safe repair may add determinable compatibility IDs/empty legacy metadata and refresh derived stale flags. It never infers facts, events, people, projects, or prose from a Markdown body.

## WebUI and API

The existing Page gains on-demand correction/revision, archive, lifecycle, reflection, and integrity views. All content continues to use DOM text nodes, bridge-authenticated plugin-local APIs, validation, and bounded result sets. Downloaded ZIPs are served only through an authenticated bridge file endpoint; no server paths or raw errors are returned.

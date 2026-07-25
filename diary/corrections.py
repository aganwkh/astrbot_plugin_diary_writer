"""Deterministic daily-fact corrections.  This module deliberately never calls an LLM."""
from __future__ import annotations

import copy
import json
import uuid
from datetime import date, datetime, timezone
from typing import Any

from .models import normalize_daily_metadata
from .maintenance import GLOBAL_MAINTENANCE_GATE, MaintenanceGate
from .reflections import mark_reflections_stale
from .reviews import ReviewService, core_fingerprint
from .storage import DiaryStorage


class CorrectionError(ValueError):
    pass


_LIST_FIELDS = {"topics", "tags", "people", "projects", "highlights", "unresolved", "ongoing_topics"}
_SCALAR_FIELDS = {"title", "mood"}


class CorrectionService:
    def __init__(self, storage: DiaryStorage, reviews: ReviewService | None = None, gate: MaintenanceGate | None = None):
        self.storage = storage
        self.reviews = reviews or ReviewService(storage)
        self.gate = gate or GLOBAL_MAINTENANCE_GATE

    def ensure_stable_ids(self, diary_date: str) -> dict[str, Any]:
        """Return normalized metadata without persisting technical IDs by itself."""
        self._validate_date(diary_date)
        metadata = self._metadata(diary_date)
        return normalize_daily_metadata(metadata)

    async def replace(self, diary_date: str, field: str, old_value: str, new_value: str, *, source: str = "command") -> dict[str, Any]:
        """Apply one deterministic correction outside archive restore windows."""
        async with self.gate.operation():
            return self._replace_unlocked(diary_date, field, old_value, new_value, source=source)

    def _replace_unlocked(self, diary_date: str, field: str, old_value: str, new_value: str, *, source: str = "command") -> dict[str, Any]:
        self._validate_date(diary_date)
        if field not in _LIST_FIELDS | _SCALAR_FIELDS:
            raise CorrectionError("field is not a deterministic correction target")
        raw_metadata = self._metadata(diary_date)
        metadata = normalize_daily_metadata(raw_metadata)
        updated = copy.deepcopy(metadata)
        target: dict[str, str] = {"field": field}
        old_text, new_text = self._text(old_value), self._text(new_value)
        if field in _LIST_FIELDS:
            values = updated.get(field)
            if not isinstance(values, list):
                values = []
            matches = [index for index, value in enumerate(values) if str(value) == old_text]
            self._require_exactly_one(matches, field)
            values[matches[0]] = new_text
            updated[field] = values
        else:
            if str(updated.get(field) or "") != old_text:
                raise CorrectionError(f"no exact match for {field}")
            updated[field] = new_text
        return self._apply(diary_date, raw_metadata, metadata, updated, target, old_text, new_text, "field_replace", source)

    async def replace_event_fact(self, diary_date: str, event_id: str, fact_id: str, old_value: str, new_value: str, *, source: str = "command") -> dict[str, Any]:
        async with self.gate.operation():
            return self._replace_event_fact_unlocked(diary_date, event_id, fact_id, old_value, new_value, source=source)

    def _replace_event_fact_unlocked(self, diary_date: str, event_id: str, fact_id: str, old_value: str, new_value: str, *, source: str = "command") -> dict[str, Any]:
        self._validate_date(diary_date)
        raw_metadata = self._metadata(diary_date)
        metadata = normalize_daily_metadata(raw_metadata)
        updated = copy.deepcopy(metadata)
        events = [item for item in updated.get("events", []) if isinstance(item, dict) and item.get("event_id") == event_id]
        self._require_exactly_one(events, "event_id")
        event = events[0]
        records = [item for item in event.get("fact_records", []) if isinstance(item, dict) and item.get("fact_id") == fact_id]
        self._require_exactly_one(records, "fact_id")
        record = records[0]
        old_text, new_text = self._text(old_value), self._text(new_value)
        if str(record.get("value") or "") != old_text:
            raise CorrectionError("fact value does not exactly match")
        record["value"] = new_text
        event["facts"] = [str(item.get("value") or "") for item in event["fact_records"] if isinstance(item, dict) and str(item.get("value") or "")]
        return self._apply(diary_date, raw_metadata, metadata, updated, {"field": "event_fact", "event_id": event_id, "fact_id": fact_id}, old_text, new_text, "event_fact_replace", source)

    async def rollback(self, diary_date: str, target_revision_id: str, *, source: str = "command") -> dict[str, Any]:
        async with self.gate.operation():
            return self._rollback_unlocked(diary_date, target_revision_id, source=source)

    def _rollback_unlocked(self, diary_date: str, target_revision_id: str, *, source: str = "command") -> dict[str, Any]:
        self._validate_rollback_target(diary_date, target_revision_id)
        current_id = str(self.storage.load_revision_state(diary_date).get("current_revision_id") or "")
        if not current_id:
            raise CorrectionError("no revision history for diary")
        target = self.storage.load_revision(diary_date, target_revision_id)
        if target is None or str(target.get("date") or "") != diary_date:
            raise CorrectionError("unknown revision")
        before, before_markdown = self._metadata(diary_date), self._markdown(diary_date)
        state_before, corrections_before = self.storage.load_revision_state(diary_date), self._corrections(diary_date)
        revisions_before = self._revision_ids(diary_date)
        correction_id = f"correction_{uuid.uuid4().hex}"
        try:
            pre = self.storage.create_revision(diary_date, operation="before_rollback", parent_revision_id=current_id)
            markdown, restored = self.storage.load_revision_contents(diary_date, target_revision_id)
            self.storage.write_diary_data(diary_date, markdown, restored, normalize=False)
            post = self.storage.create_revision(diary_date, operation="rollback", correction_id=correction_id, parent_revision_id=pre["id"], rollback_target_revision_id=target_revision_id, set_current=True)
            correction = {
                "id": correction_id, "date": diary_date, "created_at": self._now(), "source": source,
                "operation": "rollback", "target": {"revision_id": target_revision_id}, "old_value": "", "new_value": "",
                "affected_fields": [], "revision_id": post["id"], "rollback_source_revision_id": current_id,
                "rollback_target_revision_id": target_revision_id, "status": "active",
            }
            self.storage.save_correction(diary_date, correction)
            self._reconcile_statuses(diary_date, current_id, target_revision_id, correction["id"], correction["revision_id"])
        except Exception:
            self._compensate(diary_date, before_markdown, before, state_before, corrections_before, revisions_before, correction_id)
            raise
        self._mark_stale_if_changed(diary_date, before, restored, "daily_rollback")
        return correction

    def _apply(self, diary_date: str, raw_before: dict[str, Any], before: dict[str, Any], updated: dict[str, Any], target: dict[str, str], old_value: str, new_value: str, operation: str, source: str) -> dict[str, Any]:
        if before == updated:
            raise CorrectionError("correction does not change the current fact")
        state = self.storage.load_revision_state(diary_date)
        parent = str(state.get("current_revision_id") or "")
        before_markdown = self._markdown(diary_date)
        corrections_before, revisions_before = self._corrections(diary_date), self._revision_ids(diary_date)
        correction_id = f"correction_{uuid.uuid4().hex}"
        try:
            pre = self.storage.create_revision(diary_date, operation="before_correction", parent_revision_id=parent)
            markdown = self._correct_markdown(before_markdown, target["field"], old_value, new_value)
            self.storage.write_diary_data(diary_date, markdown, updated)
            post = self.storage.create_revision(diary_date, operation="correction", correction_id=correction_id, parent_revision_id=pre["id"], set_current=True)
            correction = {
                "id": correction_id, "date": diary_date, "created_at": self._now(), "source": source,
                "operation": operation, "target": target, "old_value": old_value, "new_value": new_value,
                "affected_fields": [target["field"]], "revision_id": post["id"], "parent_revision_id": pre["id"],
                "status": "active",
            }
            self.storage.save_correction(diary_date, correction)
            self._supersede_same_target(diary_date, correction)
        except Exception:
            self._compensate(diary_date, before_markdown, raw_before, state, corrections_before, revisions_before, correction_id)
            raise
        self._mark_stale_if_changed(diary_date, before, updated, "daily_corrected")
        return correction

    def _supersede_same_target(self, diary_date: str, correction: dict[str, Any]) -> None:
        signature = json.dumps(correction["target"], sort_keys=True, ensure_ascii=False)
        for previous in self.storage.iter_corrections(diary_date) or ():
            if previous.get("id") == correction["id"] or previous.get("status") != "active" or previous.get("operation") == "rollback":
                continue
            if json.dumps(previous.get("target", {}), sort_keys=True, ensure_ascii=False) == signature:
                self.storage.update_correction(diary_date, str(previous["id"]), status="superseded", superseded_by=correction["id"])

    def _reconcile_statuses(self, diary_date: str, current_revision_id: str, target_revision_id: str, rollback_correction_id: str, rollback_revision_id: str) -> None:
        """Make the ledger describe the facts restored by a rollback target."""
        current_chain = self._chain_correction_ids(diary_date, current_revision_id)
        target_chain = self._chain_correction_ids(diary_date, target_revision_id)
        restored = set(target_chain)
        for correction_id in set(current_chain) - restored:
            correction = self.storage.load_correction(diary_date, correction_id)
            if correction and correction.get("operation") != "rollback":
                self.storage.update_correction(
                    diary_date, correction_id, status="rolled_back", rolled_back_by=rollback_correction_id,
                    rolled_back_by_revision_id=rollback_revision_id,
                )
        latest_by_target: dict[str, str] = {}
        for correction_id in target_chain:
            correction = self.storage.load_correction(diary_date, correction_id)
            if not correction or correction.get("operation") == "rollback":
                continue
            signature = json.dumps(correction.get("target", {}), sort_keys=True, ensure_ascii=False)
            previous = latest_by_target.get(signature)
            if previous:
                self.storage.update_correction(diary_date, previous, status="superseded", superseded_by=correction_id)
            latest_by_target[signature] = correction_id
            self.storage.update_correction(diary_date, correction_id, status="active", superseded_by="", rolled_back_by="")

    def _chain_correction_ids(self, diary_date: str, revision_id: str) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        while revision_id and revision_id not in seen:
            seen.add(revision_id)
            revision = self.storage.load_revision(diary_date, revision_id)
            if revision is None:
                break
            # A rollback's parent preserves audit history, while its effective facts
            # are exactly those of the revision it restored.  Follow that source so
            # nested rollbacks cannot revive corrections from a discarded branch.
            if revision.get("operation") == "rollback" and revision.get("rollback_target_revision_id"):
                revision_id = str(revision["rollback_target_revision_id"])
                continue
            correction_id = str(revision.get("correction_id") or "")
            if correction_id:
                result.append(correction_id)
            revision_id = str(revision.get("parent_revision_id") or "")
        return list(reversed(result))

    def _mark_stale_if_changed(self, diary_date: str, before: dict[str, Any], after: dict[str, Any], reason: str) -> None:
        if core_fingerprint(before) != core_fingerprint(after):
            self.reviews.mark_daily_changed(date.fromisoformat(diary_date), reason)
            mark_reflections_stale(self.storage, diary_date, reason)

    def _metadata(self, diary_date: str) -> dict[str, Any]:
        metadata = self.storage.load_metadata(diary_date)
        if metadata is None:
            raise CorrectionError("daily metadata does not exist")
        return metadata

    def _markdown(self, diary_date: str) -> str:
        try:
            return self.storage.diary_path(diary_date).read_text(encoding="utf-8")
        except OSError as exc:
            raise CorrectionError("daily markdown does not exist") from exc

    @staticmethod
    def _require_exactly_one(matches: list[Any], target: str) -> None:
        if len(matches) != 1:
            raise CorrectionError(f"{target} must have exactly one exact match; found {len(matches)}")

    @staticmethod
    def _text(value: str) -> str:
        value = str(value)
        if not value:
            raise CorrectionError("empty values are not a safe correction target")
        return value

    def _validate_rollback_target(self, diary_date: str, revision_id: str) -> None:
        self._validate_date(diary_date)
        try:
            self.storage.validate_revision_id(revision_id)
        except ValueError as exc:
            raise CorrectionError("invalid revision id") from exc
        root = (self.storage.revision_root / diary_date).resolve()
        target = self.storage.revision_path(diary_date, revision_id).resolve()
        if target.parent != root:
            raise CorrectionError("revision path escapes diary history")

    def _validate_date(self, diary_date: str) -> None:
        try:
            self.storage.validate_diary_date(diary_date)
        except ValueError as exc:
            raise CorrectionError("invalid diary date") from exc

    @staticmethod
    def _correct_markdown(markdown: str, field: str, old_value: str, new_value: str) -> str:
        # Diary prose is not a structured fact field.  Never guess that one matching
        # substring is the intended claim; the explicit annotation is the safe link.
        note = f"- {field}: {old_value} → {new_value}\n"
        if "## 更正注记\n" in markdown:
            return markdown.rstrip() + "\n" + note
        return markdown.rstrip() + "\n\n## 更正注记\n" + note

    def _compensate(self, diary_date: str, markdown: str, metadata: dict[str, Any], state: dict[str, Any], corrections: dict[str, dict[str, Any]], revisions_before: set[str], correction_id: str) -> None:
        """Best-effort rollback of an unfinished correction transaction."""
        try:
            self.storage.write_diary_data(diary_date, markdown, metadata, normalize=False)
        finally:
            for revision_id in self._revision_ids(diary_date) - revisions_before:
                self.storage.delete_revision(diary_date, revision_id)
            self.storage.delete_correction(diary_date, correction_id)
            for item in corrections.values():
                self.storage.save_correction(diary_date, item)
            self.storage.save_revision_state(diary_date, state)

    def _corrections(self, diary_date: str) -> dict[str, dict[str, Any]]:
        return {str(item["id"]): copy.deepcopy(item) for item in self.storage.iter_corrections(diary_date) or () if item.get("id")}

    def _revision_ids(self, diary_date: str) -> set[str]:
        return {str(item["id"]) for item in self.storage.iter_revisions(diary_date) or () if item.get("id")}

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

"""Read-only diary integrity audit plus deterministic, fact-safe repairs."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from .models import normalize_daily_metadata
from .reflections import ReflectionService, core_fingerprint
from .storage import DiaryStorage, atomic_write_json


class IntegrityAudit:
    def __init__(self, storage: DiaryStorage):
        self.storage = storage

    def check(self) -> dict[str, Any]:
        issues: list[dict[str, Any]] = []
        markdown = {path.stem for path in self.storage.diary_root.glob("*.md")} if self.storage.diary_root.exists() else set()
        metadata = {path.stem for path in self.storage.metadata_root.glob("*.json")} if self.storage.metadata_root.exists() else set()
        for value in sorted(markdown - metadata): self._issue(issues, "missing_metadata", date=value)
        for value in sorted(metadata - markdown): self._issue(issues, "missing_markdown", date=value)
        for path in sorted(self.storage.metadata_root.glob("*.json")) if self.storage.metadata_root.exists() else ():
            self._daily(path, issues)
        self._revisions(issues)
        self._derived(issues)
        self._states(issues)
        return {"ok": not any(item["severity"] == "error" for item in issues), "issues": issues, "counts": {"errors": sum(item["severity"] == "error" for item in issues), "warnings": sum(item["severity"] == "warning" for item in issues)}}

    def safe_repair(self) -> dict[str, Any]:
        """Only add deterministic IDs and blank legacy structural fields; never infer prose facts."""
        repaired: list[str] = []
        defaults = {"title": "", "mood": "", "mood_score": None, "topics": [], "tags": [], "people": [], "projects": [], "events": [], "highlights": [], "unresolved": [], "ongoing_topics": [], "memory_ids": [], "source_count": 0}
        for path in sorted(self.storage.metadata_root.glob("*.json")) if self.storage.metadata_root.exists() else ():
            data = self._json(path)
            if data is None:
                continue
            before = json.dumps(data, ensure_ascii=False, sort_keys=True)
            data.setdefault("date", path.stem)
            for key, value in defaults.items(): data.setdefault(key, value)
            data = normalize_daily_metadata(data)
            if json.dumps(data, ensure_ascii=False, sort_keys=True) != before:
                atomic_write_json(path, data); repaired.append(path.stem)
        reflections = ReflectionService(self.storage)
        for kind, period, _ in self.storage.iter_reflection_metadata() or ():
            reflections.refresh_staleness(kind, period)
        return {"repaired_dates": repaired, "report": self.check()}

    def _daily(self, path: Path, issues: list[dict[str, Any]]) -> None:
        item = self._json(path)
        if item is None:
            return self._issue(issues, "invalid_metadata_json", date=path.stem)
        try:
            valid = date.fromisoformat(path.stem).isoformat() == path.stem
        except ValueError:
            valid = False
        if not valid or str(item.get("date") or "") != path.stem: self._issue(issues, "metadata_date_mismatch", date=path.stem)
        events = item.get("events")
        if not isinstance(events, list): return self._issue(issues, "invalid_events_schema", date=path.stem)
        known_memory = {str(value) for value in item.get("memory_ids", []) if str(value)}
        event_ids: set[str] = set()
        for event in events:
            if not isinstance(event, dict): self._issue(issues, "invalid_event", date=path.stem); continue
            event_id = str(event.get("event_id") or "")
            if not event_id or event_id in event_ids: self._issue(issues, "invalid_event_id", date=path.stem, event_id=event_id)
            event_ids.add(event_id)
            fact_ids: set[str] = set()
            for fact in event.get("fact_records", []) if isinstance(event.get("fact_records"), list) else []:
                fact_id = str(fact.get("fact_id") or "") if isinstance(fact, dict) else ""
                if not fact_id or fact_id in fact_ids: self._issue(issues, "invalid_fact_id", date=path.stem, event_id=event_id)
                fact_ids.add(fact_id)
            dangling = {str(value) for value in event.get("memory_ids", []) if str(value)} - known_memory
            if dangling: self._issue(issues, "dangling_evidence", date=path.stem, event_id=event_id, memory_ids=sorted(dangling))

    def _revisions(self, issues: list[dict[str, Any]]) -> None:
        for path in sorted(self.storage.revision_state_root.glob("*.json")) if self.storage.revision_state_root.exists() else ():
            value = path.stem
            try: state = self.storage.load_revision_state(value)
            except ValueError: self._issue(issues, "invalid_revision_state", date=value); continue
            current = str(state.get("current_revision_id") or "")
            if current and self.storage.load_revision(value, current) is None: self._issue(issues, "missing_current_revision", date=value)
        for root in self.storage.revision_root.iterdir() if self.storage.revision_root.exists() else ():
            if not root.is_dir(): continue
            known = {str(item.get("id")) for item in self.storage.iter_revisions(root.name) or ()}
            for revision in self.storage.iter_revisions(root.name) or ():
                parent = str(revision.get("parent_revision_id") or "")
                if parent and parent not in known: self._issue(issues, "missing_revision_parent", date=root.name, revision_id=str(revision.get("id") or ""))
            for correction in self.storage.iter_corrections(root.name) or ():
                revision_id = str(correction.get("revision_id") or "")
                if revision_id and revision_id not in known: self._issue(issues, "missing_correction_revision", date=root.name, correction_id=str(correction.get("id") or ""))
        for root in self.storage.correction_root.iterdir() if self.storage.correction_root.exists() else ():
            if not root.is_dir() or (self.storage.revision_root / root.name).exists():
                continue
            for correction in self.storage.iter_corrections(root.name) or ():
                if str(correction.get("revision_id") or ""):
                    self._issue(issues, "missing_correction_revision", date=root.name, correction_id=str(correction.get("id") or ""))

    def _derived(self, issues: list[dict[str, Any]]) -> None:
        self._pair_issues(self.storage.reflection_root, self.storage.reflection_metadata_root, "reflection", issues)
        for kind, period, item in self.storage.iter_reflection_metadata() or ():
            if not item.get("subjective"): self._issue(issues, "reflection_not_subjective", kind=kind, period=period)
            if item.get("reflection_stale"): self._issue(issues, "stale_reflection", severity="warning", kind=kind, period=period)
            self._fingerprint_issues(item, "reflection", kind, period, issues)
            self._reflection_ref_issues(item, kind, period, issues)

    def _fingerprint_issues(self, item: dict[str, Any], label: str, kind: str, period: str, issues: list[dict[str, Any]]) -> None:
        fingerprints = item.get("source_fingerprints")
        if not isinstance(fingerprints, dict):
            return
        for value, expected in fingerprints.items():
            current = self.storage.load_metadata(str(value))
            if current is None or core_fingerprint(current) != expected:
                self._issue(issues, f"{label}_source_fingerprint_mismatch", severity="warning", kind=kind, period=period, date=str(value))

    def _reflection_ref_issues(self, item: dict[str, Any], kind: str, period: str, issues: list[dict[str, Any]]) -> None:
        allowed = {"title", "mood", "topics", "people", "projects", "highlights", "unresolved", "ongoing_topics", "events"}
        refs = item.get("source_refs")
        if not isinstance(refs, list):
            return self._issue(issues, "invalid_reflection_source_refs", kind=kind, period=period)
        for ref in refs:
            if not isinstance(ref, dict):
                self._issue(issues, "invalid_reflection_source_ref", kind=kind, period=period); continue
            value, field = str(ref.get("date") or ""), str(ref.get("field") or "")
            daily = self.storage.load_metadata(value) if field in allowed else None
            if daily is None or (field != "events" and field not in daily):
                self._issue(issues, "dangling_reflection_source_ref", kind=kind, period=period, date=value, field=field); continue
            event_id, fact_id = str(ref.get("event_id") or ""), str(ref.get("fact_id") or "")
            if fact_id and not event_id:
                self._issue(issues, "dangling_reflection_fact_ref", kind=kind, period=period, date=value, fact_id=fact_id); continue
            if not event_id:
                continue
            matches = [event for event in daily.get("events", []) if isinstance(event, dict) and str(event.get("event_id") or "") == event_id] if isinstance(daily.get("events"), list) else []
            if len(matches) != 1:
                self._issue(issues, "dangling_reflection_event_ref", kind=kind, period=period, date=value, event_id=event_id); continue
            if fact_id:
                records = matches[0].get("fact_records") if isinstance(matches[0].get("fact_records"), list) else []
                if sum(isinstance(record, dict) and str(record.get("fact_id") or "") == fact_id for record in records) != 1:
                    self._issue(issues, "dangling_reflection_fact_ref", kind=kind, period=period, date=value, event_id=event_id, fact_id=fact_id)

    def _pair_issues(self, markdown_root: Path, metadata_root: Path, label: str, issues: list[dict[str, Any]]) -> None:
        markdown = {path.relative_to(markdown_root).with_suffix("").as_posix() for path in markdown_root.rglob("*.md")} if markdown_root.exists() else set()
        metadata = {path.relative_to(metadata_root).with_suffix("").as_posix() for path in metadata_root.rglob("*.json")} if metadata_root.exists() else set()
        for value in sorted(markdown - metadata): self._issue(issues, f"missing_{label}_metadata", period=value)
        for value in sorted(metadata - markdown): self._issue(issues, f"missing_{label}_markdown", period=value)

    def _states(self, issues: list[dict[str, Any]]) -> None:
        for name in ("generation_state.json", "reflection_generation_state.json"):
            path = self.storage.root / name
            if path.exists() and self._json(path) is None: self._issue(issues, "invalid_generation_state", file=name)
        # Archives are untrusted external inputs even when kept locally.  Reuse the
        # archive verifier so manifest failures are visible without restoring data.
        from .archives import ArchiveError, ArchiveService
        verifier = ArchiveService(self.storage)
        for root in (verifier.export_root, verifier.pre_restore_root):
            if not root.exists():
                continue
            for path in root.glob("*.zip"):
                try:
                    verifier.verify(path)
                except ArchiveError:
                    self._issue(issues, "invalid_backup_manifest", file=str(path.name))

    @staticmethod
    def _json(path: Path) -> dict[str, Any] | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8")); return value if isinstance(value, dict) else None
        except (OSError, json.JSONDecodeError): return None

    @staticmethod
    def _issue(issues: list[dict[str, Any]], code: str, severity: str = "error", **detail: Any) -> None:
        issues.append({"code": code, "severity": severity, **detail})

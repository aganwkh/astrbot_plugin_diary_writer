import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from diary.corrections import CorrectionService
from diary.archives import ArchiveService
from diary.integrity import IntegrityAudit
from diary.lifecycle import lifecycle
from diary.reflections import ReflectionService
from diary.reviews import ReviewService, core_fingerprint, review_fingerprint
from diary.storage import DiaryStorage, atomic_write_json, atomic_write_text


def write_daily(storage, value, *, people=None, projects=None, events=None):
    atomic_write_text(storage.diary_path(value), f"# {value}\n")
    atomic_write_json(storage.metadata_path(value), {
        "date": value, "title": value, "topics": ["AstrBot"], "people": people or [], "projects": projects or [],
        "events": events or [], "highlights": [], "unresolved": [], "memory_ids": ["m-1"],
    })


class Provider:
    async def text_chat(self, **_kwargs):
        return type("Result", (), {"completion_text": json.dumps({"markdown": "# subjective", "reflection": "I feel the recorded work moved forward."})})()


class DerivedV06Tests(unittest.TestCase):
    def test_lifecycle_scans_current_daily_with_reappearance_and_observational_status(self):
        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp))
            write_daily(storage, "2024-01-01", people=["Alice"], projects=["Godot"], events=[{"summary": "Godot work", "facts": ["Godot"], "memory_ids": ["m-1"]}, {"summary": "unrelated cooking", "facts": ["rice"], "memory_ids": ["m-1"]}])
            write_daily(storage, "2024-01-02", people=["Alice"], projects=["Godot"])
            write_daily(storage, "2024-03-01", people=["Alice"], projects=["Godot"])
            result = lifecycle(storage, "godot", "projects", as_of="2024-04-15")
            self.assertEqual(result["first_seen"], "2024-01-01")
            self.assertEqual(result["coverage_count"], 3)
            self.assertEqual(result["occurrence_count"], 3)
            self.assertEqual(result["continuous_intervals"], [{"start": "2024-01-01", "end": "2024-01-02"}, {"start": "2024-03-01", "end": "2024-03-01"}])
            self.assertEqual(result["observational_status"], "recent_not_observed")
            self.assertEqual([item["summary"] for item in result["key_related_events"]], ["Godot work"])

    def test_lifecycle_default_as_of_marks_old_record_not_observed(self):
        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp)); write_daily(storage, "2000-01-01", projects=["Old"])
            self.assertEqual(lifecycle(storage, "Old", "projects")["observational_status"], "recent_not_observed")

    def test_reflection_is_subjective_and_stales_on_corrected_source(self):
        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp))
            write_daily(storage, "2024-01-01", projects=["Old"], events=[{"summary": "Old work", "facts": ["Old fact"], "memory_ids": ["m-1"]}])
            IntegrityAudit(storage).safe_repair()
            path = asyncio.run(ReflectionService(storage).generate("monthly", "2024-01", Provider()))
            self.assertTrue(path)
            metadata = storage.load_reflection_metadata("monthly", "2024-01")
            self.assertTrue(metadata["subjective"])
            self.assertTrue(any(ref.get("event_id") for ref in metadata["source_refs"]))
            self.assertNotIn("reflection", storage.load_metadata("2024-01-01"))
            asyncio.run(CorrectionService(storage).replace("2024-01-01", "projects", "Old", "New"))
            self.assertTrue(storage.load_reflection_metadata("monthly", "2024-01")["reflection_stale"])

    def test_audit_detects_yearly_monthly_and_reflection_stable_reference_damage(self):
        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp))
            write_daily(storage, "2024-01-01", events=[{"summary": "work", "facts": ["fact"], "memory_ids": ["m-1"]}])
            IntegrityAudit(storage).safe_repair()
            event = storage.load_metadata("2024-01-01")["events"][0]
            monthly = {"title": "January", "topics": [], "people": [], "projects": [], "events": [], "highlights": [], "unresolved": []}
            storage.write_review("monthly", "2024-01", "# January", monthly)
            storage.write_review("yearly", "2024", "# Year", {"summary_stale": False, "source_monthly_fingerprints": {"2024-01": review_fingerprint(monthly)}})
            storage.write_reflection("monthly", "2024-01", "# Reflection", {"subjective": True, "reflection_stale": False, "source_refs": [{"date": "2024-01-01", "field": "events", "event_id": event["event_id"], "fact_id": event["fact_records"][0]["fact_id"]}, {"date": "2024-01-01", "field": "events", "event_id": "event_missing"}]})
            monthly["title"] = "Changed"; storage.write_review("monthly", "2024-01", "# Changed", monthly)
            codes = {item["code"] for item in IntegrityAudit(storage).check()["issues"]}
            self.assertIn("yearly_monthly_fingerprint_mismatch", codes)
            self.assertIn("dangling_reflection_event_ref", codes)

    def test_integrity_finds_fixture_damage_and_safe_repair_only_adds_structure(self):
        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp))
            atomic_write_text(storage.diary_path("2024-01-01"), "# legacy\n")
            atomic_write_json(storage.metadata_path("2024-01-02"), {"date": "2024-01-02", "events": [{"summary": "bad", "memory_ids": ["missing"]}]})
            report = IntegrityAudit(storage).check()
            codes = {item["code"] for item in report["issues"]}
            self.assertIn("missing_metadata", codes)
            self.assertIn("missing_markdown", codes)
            self.assertIn("invalid_event_id", codes)
            self.assertIn("dangling_evidence", codes)
            repaired = IntegrityAudit(storage).safe_repair()
            self.assertIn("2024-01-02", repaired["repaired_dates"])
            data = storage.load_metadata("2024-01-02")
            self.assertEqual(data["projects"], [])
            self.assertEqual(data["events"][0]["facts"], [])

    def test_v03_shaped_daily_is_readable_without_inference(self):
        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp))
            write_daily(storage, "2023-07-25", people=["Alice"], projects=["Legacy"], events=[{"summary": "legacy", "facts": ["only explicit"], "memory_ids": ["m-1"]}])
            result = lifecycle(storage, "Legacy", "projects")
            self.assertEqual(result["coverage_dates"], ["2023-07-25"])
            IntegrityAudit(storage).safe_repair()
            event = storage.load_metadata("2023-07-25")["events"][0]
            self.assertTrue(event["event_id"])
            self.assertEqual(event["facts"], ["only explicit"])

    def test_v03_to_v05_daily_metadata_remains_readable_and_exportable(self):
        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp))
            write_daily(storage, "2023-01-01", projects=["v03"], events=[{"summary": "old", "facts": ["explicit"], "memory_ids": ["m-1"]}])
            write_daily(storage, "2024-01-01", projects=["v04"], events=[{"summary": "reviewable", "facts": ["explicit"], "memory_ids": ["m-1"]}])
            write_daily(storage, "2025-01-01", projects=["v05"], events=[{"summary": "observable", "facts": ["explicit"], "memory_ids": ["m-1"]}])
            current = storage.load_metadata("2025-01-01"); current.update({"mood": "calm", "mood_score": 4, "generated_at": "2025-01-01T00:00:00+00:00"})
            atomic_write_json(storage.metadata_path("2025-01-01"), current)
            IntegrityAudit(storage).safe_repair()
            self.assertEqual(lifecycle(storage, "v03", "projects")["first_seen"], "2023-01-01")
            archive = asyncio.run(ArchiveService(storage).export())
            self.assertEqual(ArchiveService(storage).verify(archive)["plugin_data_format"], "v0.3-v1.1")

    def test_technical_ids_do_not_change_fingerprint_but_audit_finds_real_source_change(self):
        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp))
            write_daily(storage, "2024-01-01", projects=["Old"], events=[{"summary": "work", "facts": ["Old"], "memory_ids": ["m-1"]}])
            legacy = storage.load_metadata("2024-01-01")
            fingerprint = core_fingerprint(legacy)
            IntegrityAudit(storage).safe_repair()
            self.assertEqual(fingerprint, core_fingerprint(storage.load_metadata("2024-01-01")))
            storage.write_review("monthly", "2024-01", "# review", {"summary_stale": False, "source_fingerprints": {"2024-01-01": fingerprint}})
            storage.write_reflection("monthly", "2024-01", "# reflection", {"subjective": True, "reflection_stale": False, "source_fingerprints": {"2024-01-01": fingerprint}, "source_refs": [{"date": "2024-01-01", "field": "projects"}]})
            changed = storage.load_metadata("2024-01-01"); changed["projects"] = ["New"]; atomic_write_json(storage.metadata_path("2024-01-01"), changed)
            codes = {item["code"] for item in IntegrityAudit(storage).check()["issues"]}
            self.assertIn("review_source_fingerprint_mismatch", codes)
            self.assertIn("reflection_source_fingerprint_mismatch", codes)
            IntegrityAudit(storage).safe_repair()
            self.assertTrue(storage.load_review_metadata("monthly", "2024-01")["summary_stale"])
            self.assertTrue(storage.load_reflection_metadata("monthly", "2024-01")["reflection_stale"])


if __name__ == "__main__":
    unittest.main()

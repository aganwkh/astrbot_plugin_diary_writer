import asyncio
import tempfile
import unittest
from datetime import date
from pathlib import Path

from diary.corrections import CorrectionError, CorrectionService as AsyncCorrectionService
from diary.reviews import ReviewService
from diary.storage import DiaryStorage, atomic_write_json, atomic_write_text


class CorrectionService(AsyncCorrectionService):
    """Keep deterministic unit fixtures concise while production mutators are async."""

    def replace(self, *args, **kwargs):
        return asyncio.run(super().replace(*args, **kwargs))

    def replace_event_fact(self, *args, **kwargs):
        return asyncio.run(super().replace_event_fact(*args, **kwargs))

    def rollback(self, *args, **kwargs):
        return asyncio.run(super().rollback(*args, **kwargs))


def write_daily(storage, value="2025-07-25", markdown="Alice shipped Alpha once."):
    atomic_write_text(storage.diary_path(value), markdown)
    atomic_write_json(storage.metadata_path(value), {
        "date": value, "title": "Alice diary", "people": ["Alice"], "projects": ["Alpha"],
        "topics": ["Diary"], "events": [{"summary": "Alice shipped Alpha", "facts": ["Alpha shipped"], "memory_ids": ["m-1"]}],
    })


class CorrectionsTests(unittest.TestCase):
    def test_stable_event_and_fact_ids_and_exact_field_replace(self):
        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp)); write_daily(storage)
            result = CorrectionService(storage).replace("2025-07-25", "projects", "Alpha", "Beta")
            current = storage.load_metadata("2025-07-25")
            event = current["events"][0]
            self.assertEqual(current["projects"], ["Beta"])
            self.assertTrue(event["event_id"].startswith("event_"))
            self.assertEqual(event["facts"], ["Alpha shipped"])
            self.assertEqual(event["fact_records"][0]["value"], "Alpha shipped")
            self.assertTrue(event["fact_records"][0]["fact_id"].startswith("fact_"))
            self.assertEqual(result["status"], "active")

    def test_zero_or_multiple_match_rejects_without_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp)); write_daily(storage)
            service = CorrectionService(storage)
            before = storage.metadata_path("2025-07-25").read_text(encoding="utf-8")
            with self.assertRaises(CorrectionError): service.replace("2025-07-25", "projects", "missing", "Beta")
            self.assertEqual(list(storage.iter_revisions("2025-07-25") or ()), [])
            self.assertEqual(list(storage.iter_corrections("2025-07-25") or ()), [])
            current = storage.load_metadata("2025-07-25"); current["projects"] = ["Alpha", "Alpha"]
            before_core = {key: current[key] for key in ("date", "title", "people", "projects", "topics", "events")}
            atomic_write_json(storage.metadata_path("2025-07-25"), current)
            with self.assertRaises(CorrectionError): service.replace("2025-07-25", "projects", "Alpha", "Beta")
            self.assertEqual(storage.load_metadata("2025-07-25")["projects"], ["Alpha", "Alpha"])
            self.assertEqual({key: storage.load_metadata("2025-07-25")[key] for key in before_core}, before_core)
            self.assertEqual(list(storage.iter_revisions("2025-07-25") or ()), [])
            self.assertEqual(list(storage.iter_corrections("2025-07-25") or ()), [])
            self.assertIn("Alpha", before)

    def test_event_fact_replacement_uses_stable_ids_not_index(self):
        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp)); write_daily(storage)
            service = CorrectionService(storage)
            event = service.ensure_stable_ids("2025-07-25")["events"][0]
            result = service.replace_event_fact("2025-07-25", event["event_id"], event["fact_records"][0]["fact_id"], "Alpha shipped", "Beta shipped")
            updated = storage.load_metadata("2025-07-25")["events"][0]
            self.assertEqual(updated["facts"], ["Beta shipped"])
            self.assertEqual(updated["fact_records"][0]["value"], "Beta shipped")
            self.assertEqual(result["target"]["event_id"], event["event_id"])
            self.assertNotIn("event_index", result["target"])

    def test_revision_chain_rollback_and_correction_statuses(self):
        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp)); write_daily(storage)
            service = CorrectionService(storage)
            first = service.replace("2025-07-25", "projects", "Alpha", "Beta")
            second = service.replace("2025-07-25", "projects", "Beta", "Gamma")
            self.assertEqual(storage.load_correction("2025-07-25", first["id"])["status"], "superseded")
            state = storage.load_revision_state("2025-07-25")
            self.assertEqual(state["current_revision_id"], second["revision_id"])
            rollback = service.rollback("2025-07-25", first["revision_id"])
            self.assertEqual(storage.load_metadata("2025-07-25")["projects"], ["Beta"])
            self.assertEqual(storage.load_correction("2025-07-25", first["id"])["status"], "active")
            rolled_back = storage.load_correction("2025-07-25", second["id"])
            self.assertEqual(rolled_back["status"], "rolled_back")
            self.assertEqual(rolled_back["rolled_back_by"], rollback["id"])
            self.assertEqual(rolled_back["rolled_back_by_revision_id"], rollback["revision_id"])
            current = storage.load_revision_state("2025-07-25")["current_revision_id"]
            record = storage.load_revision("2025-07-25", current)
            self.assertEqual(record["rollback_target_revision_id"], first["revision_id"])
            self.assertEqual(rollback["revision_id"], current)

    def test_nested_rollback_uses_restored_effective_facts_not_audit_parent_branch(self):
        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp)); write_daily(storage)
            service = CorrectionService(storage)
            alpha_to_beta = service.replace("2025-07-25", "projects", "Alpha", "Beta")
            beta_to_gamma = service.replace("2025-07-25", "projects", "Beta", "Gamma")
            rollback_beta = service.rollback("2025-07-25", alpha_to_beta["revision_id"])
            service.rollback("2025-07-25", rollback_beta["revision_id"])
            self.assertEqual(storage.load_metadata("2025-07-25")["projects"], ["Beta"])
            self.assertEqual(storage.load_correction("2025-07-25", alpha_to_beta["id"])["status"], "active")
            self.assertEqual(storage.load_correction("2025-07-25", beta_to_gamma["id"])["status"], "rolled_back")

    def test_markdown_ambiguous_match_uses_correction_annotation(self):
        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp)); write_daily(storage, markdown="Alpha then Alpha")
            CorrectionService(storage).replace("2025-07-25", "projects", "Alpha", "Beta")
            markdown = storage.diary_path("2025-07-25").read_text(encoding="utf-8")
            self.assertIn("更正注记", markdown)
            self.assertIn("projects", markdown)
            self.assertEqual(markdown.count("Beta"), 1)

    def test_markdown_single_unrelated_occurrence_also_uses_annotation(self):
        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp)); write_daily(storage, markdown="Alpha appears once in prose.")
            CorrectionService(storage).replace("2025-07-25", "projects", "Alpha", "Beta")
            markdown = storage.diary_path("2025-07-25").read_text(encoding="utf-8")
            self.assertIn("Alpha appears once in prose.", markdown)
            self.assertIn("## 更正注记", markdown)
            self.assertIn("projects: Alpha → Beta", markdown)

    def test_rejection_leaves_raw_metadata_unchanged_and_first_snapshot_preserves_it(self):
        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp)); write_daily(storage)
            raw = storage.metadata_path("2025-07-25").read_text(encoding="utf-8")
            service = CorrectionService(storage)
            with self.assertRaises(CorrectionError): service.replace("2025-07-25", "projects", "missing", "Beta")
            self.assertEqual(storage.metadata_path("2025-07-25").read_text(encoding="utf-8"), raw)
            correction = service.replace("2025-07-25", "projects", "Alpha", "Beta")
            _, snapshot = storage.load_revision_contents("2025-07-25", correction["parent_revision_id"])
            self.assertNotIn("event_id", snapshot["events"][0])
            self.assertNotIn("fact_records", snapshot["events"][0])
            self.assertIn("event_id", storage.load_metadata("2025-07-25")["events"][0])

    def test_actual_fact_change_marks_existing_week_month_and_year_stale(self):
        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp)); write_daily(storage)
            for kind, period in (("weekly", "2025-W30"), ("monthly", "2025-07"), ("yearly", "2025")):
                storage.write_review(kind, period, "# review", {"summary_stale": False})
            CorrectionService(storage, ReviewService(storage)).replace("2025-07-25", "projects", "Alpha", "Beta")
            self.assertTrue(all(storage.load_review_metadata(kind, period)["summary_stale"] for kind, period in (("weekly", "2025-W30"), ("monthly", "2025-07"), ("yearly", "2025"))))

    def test_correction_post_snapshot_failure_restores_current_and_removes_partial_history(self):
        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp)); write_daily(storage)
            service = CorrectionService(storage)
            original = storage.create_revision
            calls = 0

            def fail_post(*args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("revision disk full")
                return original(*args, **kwargs)

            storage.create_revision = fail_post
            with self.assertRaises(OSError): service.replace("2025-07-25", "projects", "Alpha", "Beta")
            self.assertEqual(storage.load_metadata("2025-07-25")["projects"], ["Alpha"])
            self.assertEqual(storage.diary_path("2025-07-25").read_text(encoding="utf-8"), "Alice shipped Alpha once.")
            self.assertEqual(list(storage.iter_revisions("2025-07-25") or ()), [])
            self.assertEqual(list(storage.iter_corrections("2025-07-25") or ()), [])
            self.assertEqual(storage.load_revision_state("2025-07-25")["current_revision_id"], "")

    def test_rollback_post_snapshot_failure_preserves_current_and_history(self):
        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp)); write_daily(storage)
            service = CorrectionService(storage)
            first = service.replace("2025-07-25", "projects", "Alpha", "Beta")
            second = service.replace("2025-07-25", "projects", "Beta", "Gamma")
            state_before = storage.load_revision_state("2025-07-25")
            revision_ids_before = {item["id"] for item in storage.iter_revisions("2025-07-25")}
            original = storage.create_revision

            def fail_rollback(*args, **kwargs):
                if kwargs.get("operation") == "rollback":
                    raise OSError("revision disk full")
                return original(*args, **kwargs)

            storage.create_revision = fail_rollback
            with self.assertRaises(OSError): service.rollback("2025-07-25", first["revision_id"])
            self.assertEqual(storage.load_metadata("2025-07-25")["projects"], ["Gamma"])
            self.assertEqual(storage.load_revision_state("2025-07-25"), state_before)
            self.assertEqual({item["id"] for item in storage.iter_revisions("2025-07-25")}, revision_ids_before)
            self.assertEqual(storage.load_correction("2025-07-25", second["id"])["status"], "active")

    def test_rollback_rejects_traversal_and_cross_date_revision(self):
        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp)); write_daily(storage)
            service = CorrectionService(storage)
            first = service.replace("2025-07-25", "projects", "Alpha", "Beta")
            with self.assertRaises(CorrectionError):
                service.rollback("2025-07-25", "../" + first["revision_id"])
            write_daily(storage, "2025-07-26")
            foreign = service.replace("2025-07-26", "projects", "Alpha", "Elsewhere")
            with self.assertRaises(CorrectionError):
                service.rollback("2025-07-25", foreign["revision_id"])
            self.assertEqual(storage.load_metadata("2025-07-25")["projects"], ["Beta"])

    def test_public_correction_apis_reject_date_traversal_before_storage_access(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            storage = DiaryStorage(root); service = CorrectionService(storage)
            escaped = root.parent / "escaped.json"
            for action in (
                lambda: service.ensure_stable_ids("../../escaped"),
                lambda: service.replace("../../escaped", "projects", "old", "new"),
                lambda: service.replace_event_fact("../../escaped", "event_x", "fact_x", "old", "new"),
            ):
                with self.assertRaises(CorrectionError):
                    action()
            self.assertFalse(escaped.exists())

    def test_storage_revision_and_correction_helpers_reject_traversal_directly(self):
        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp))
            revision = "rev_" + "a" * 32
            correction = "correction_" + "b" * 32
            with self.assertRaises(ValueError): storage.revision_path("../../escaped", revision)
            with self.assertRaises(ValueError): storage.correction_path("2025-07-25", "../" + correction)
            with self.assertRaises(ValueError): storage.load_revision("2025-07-25", "../" + revision)
            with self.assertRaises(ValueError): storage.load_correction("../../escaped", correction)

    def test_storage_rejects_foreign_or_missing_parent_and_current_revision_pointer(self):
        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp)); write_daily(storage)
            missing = "rev_" + "a" * 32
            with self.assertRaises(ValueError):
                storage.create_revision("2025-07-25", operation="test", parent_revision_id=missing)
            write_daily(storage, "2025-07-26")
            foreign = storage.create_revision("2025-07-26", operation="test", set_current=True)
            with self.assertRaises(ValueError):
                storage.create_revision("2025-07-25", operation="test", parent_revision_id=foreign["id"])
            atomic_write_json(storage.revision_state_path("2025-07-25"), {"date": "2025-07-25", "current_revision_id": foreign["id"]})
            with self.assertRaises(ValueError):
                storage.load_revision_state("2025-07-25")


if __name__ == "__main__":
    unittest.main()

import math
import tempfile
import unittest
from pathlib import Path

from diary.storage import DiaryStorage, atomic_write_json


def write_daily(storage, value, **changes):
    metadata = {
        "date": value,
        "title": value,
        "mood_score": None,
        "topics": [],
        "projects": [],
        "events": [],
        "unresolved": [],
    }
    metadata.update(changes)
    atomic_write_json(storage.metadata_path(value), metadata)


class TrendTests(unittest.TestCase):
    def test_empty_and_invalid_ranges_are_stable_or_rejected(self):
        from diary.trends import build_trends

        with tempfile.TemporaryDirectory() as temp:
            result = build_trends(DiaryStorage(Path(temp)))
            self.assertEqual(result["mood_points"], [])
            self.assertEqual(result["mood_categories"], [])
            self.assertEqual(result["mood_counts"], [])
            self.assertEqual(result["monthly"], [])
            self.assertEqual(result["topics"], [])
            self.assertEqual(result["projects"], [])
            self.assertEqual(result["project_activity"], [])
            with self.assertRaises(ValueError):
                build_trends(DiaryStorage(Path(temp)), start="not-a-date")
            with self.assertRaises(ValueError):
                build_trends(DiaryStorage(Path(temp)), start="2025-02-01", end="2025-01-01")

    def test_daily_facts_aggregate_without_mutating_metadata(self):
        from diary.trends import build_trends

        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp))
            write_daily(storage, "2025-01-02", mood="Calm", mood_score=0.5, topics=["AstrBot", "astrbot", ""], projects=["Godot", "godot"], events=[{"summary": "one"}, "malformed", {"summary": "two"}], unresolved=["one", "two"])
            write_daily(storage, "2025-02-03", mood="calm", mood_score=1, topics=["ASTRBOT", "Python"], projects=["Godot", "Diary"], events=[{"summary": "three"}], unresolved=["one"])
            write_daily(storage, "2025-03-03", mood_score="bad", topics="wrong", projects=None, events="wrong", unresolved={})

            result = build_trends(storage, start="2025-01-01", end="2025-02-28")

            self.assertEqual(result["mood_points"], [{"date": "2025-01-02", "mood_score": 0.5}, {"date": "2025-02-03", "mood_score": 1.0}])
            self.assertEqual(result["mood_categories"], [{"date": "2025-01-02", "mood": "Calm"}, {"date": "2025-02-03", "mood": "calm"}])
            self.assertEqual(result["mood_counts"], [{"mood": "Calm", "count": 2}])
            self.assertEqual(result["monthly"], [
                {"month": "2025-01", "diary_count": 1, "event_count": 2, "unresolved_count": 2, "mood_score_average": 0.5},
                {"month": "2025-02", "diary_count": 1, "event_count": 1, "unresolved_count": 1, "mood_score_average": 1.0},
            ])
            self.assertEqual(result["topics"], [{"name": "AstrBot", "count": 2}, {"name": "Python", "count": 1}])
            self.assertEqual(result["projects"], [{"name": "Godot", "count": 2}, {"name": "Diary", "count": 1}])
            self.assertEqual(result["project_activity"], [
                {"month": "2025-01", "observed": ["Godot"], "added": ["Godot"], "absent": []},
                {"month": "2025-02", "observed": ["Diary", "Godot"], "added": ["Diary"], "absent": []},
            ])
            self.assertEqual(storage.load_metadata("2025-01-02")["topics"], ["AstrBot", "astrbot", ""])

    def test_project_activity_records_observed_disappearances(self):
        from diary.trends import build_trends

        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp))
            write_daily(storage, "2025-01-01", projects=["Godot"])
            write_daily(storage, "2025-02-01", projects=["Diary"])
            write_daily(storage, "2025-03-01", projects=["Diary"])

            self.assertEqual(build_trends(storage)["project_activity"], [
                {"month": "2025-01", "observed": ["Godot"], "added": ["Godot"], "absent": []},
                {"month": "2025-02", "observed": ["Diary"], "added": ["Diary"], "absent": ["Godot"]},
                {"month": "2025-03", "observed": ["Diary"], "added": [], "absent": []},
            ])

    def test_non_finite_mood_scores_do_not_pollute_trends_or_averages(self):
        from diary.trends import build_trends

        with tempfile.TemporaryDirectory() as temp:
            storage = DiaryStorage(Path(temp))
            write_daily(storage, "2025-01-01", mood_score=0.25)
            write_daily(storage, "2025-01-02", mood_score=math.nan)
            write_daily(storage, "2025-01-03", mood_score=math.inf)
            write_daily(storage, "2025-01-04", mood_score=-math.inf)

            result = build_trends(storage)

            self.assertEqual(result["mood_points"], [{"date": "2025-01-01", "mood_score": 0.25}])
            self.assertEqual(result["monthly"][0]["mood_score_average"], 0.25)

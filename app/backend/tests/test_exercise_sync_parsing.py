"""Unit tests for routers/sync.py's pure exercise-sync helper functions
(_exercise_row_source_id, _exercise_row_to_contract). These are the
glue between parse_exercises_csv() (tested in
test_parse_exercises_csv.py) and log_exercise_entry() (the contract
write path) -- isolated here so they're directly testable without a DB
connection or async/event-loop context (see the earlier note in
test_exercise_log.py about why this project doesn't call async DB
functions directly from sync test code)."""
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.routers.sync import _exercise_row_source_id, _exercise_row_to_contract  # noqa: E402


class TestExerciseRowSourceId:
    def test_same_inputs_produce_same_id(self):
        """Re-syncing the same historical row must re-derive the SAME
        source_id every time, so log_exercise_entry()'s dedupe actually
        recognizes it as already-imported."""
        a = _exercise_row_source_id("2026-07-21", "Martial Arts", 60, 288.8)
        b = _exercise_row_source_id("2026-07-21", "Martial Arts", 60, 288.8)
        assert a == b

    def test_different_date_produces_different_id(self):
        a = _exercise_row_source_id("2026-07-21", "Martial Arts", 60, 288.8)
        b = _exercise_row_source_id("2026-07-22", "Martial Arts", 60, 288.8)
        assert a != b

    def test_different_activity_produces_different_id(self):
        a = _exercise_row_source_id("2026-07-21", "Martial Arts", 60, 288.8)
        b = _exercise_row_source_id("2026-07-21", "Running", 60, 288.8)
        assert a != b

    def test_different_duration_or_calories_produces_different_id(self):
        """Two genuinely different real entries on the same day/activity
        (same activity logged twice with different duration/calories)
        must NOT collide into one deduped id."""
        a = _exercise_row_source_id("2026-07-21", "Martial Arts", 60, 288.8)
        b = _exercise_row_source_id("2026-07-21", "Martial Arts", 40, 192.5)
        assert a != b


class TestExerciseRowToContract:
    def test_valid_row_produces_contract(self):
        row = {"date": "2026-07-21", "activity_name": "Martial Arts", "duration_minutes": 60, "calories_burned": 288.8}
        entry = _exercise_row_to_contract(row)
        assert entry is not None
        assert entry.date == "2026-07-21"
        assert entry.activity_name == "Martial Arts"
        assert entry.duration_minutes == 60
        assert entry.calories_burned == 288.8
        assert entry.source == "Cronometer"
        assert entry.source_id is not None

    def test_missing_date_skips_row(self):
        row = {"date": None, "activity_name": "Martial Arts", "duration_minutes": 60, "calories_burned": 288.8}
        assert _exercise_row_to_contract(row) is None

    def test_missing_activity_name_skips_row(self):
        row = {"date": "2026-07-21", "activity_name": None, "duration_minutes": 60, "calories_burned": 288.8}
        assert _exercise_row_to_contract(row) is None

    def test_missing_calories_defaults_to_zero(self):
        row = {"date": "2026-07-21", "activity_name": "Walking", "duration_minutes": 20, "calories_burned": None}
        entry = _exercise_row_to_contract(row)
        assert entry.calories_burned == 0

    def test_two_rows_with_same_fields_produce_same_source_id(self):
        """Confirms the contract-building path (not just the raw
        source_id function) is deterministic end-to-end -- re-syncing
        the same CSV row twice must produce a contract that
        log_exercise_entry() will actually recognize as a duplicate."""
        row = {"date": "2026-07-21", "activity_name": "Martial Arts", "duration_minutes": 60, "calories_burned": 288.8}
        entry1 = _exercise_row_to_contract(row)
        entry2 = _exercise_row_to_contract(dict(row))
        assert entry1.source_id == entry2.source_id

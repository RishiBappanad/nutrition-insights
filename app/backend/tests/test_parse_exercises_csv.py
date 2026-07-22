"""Unit tests for integrations/cronometer_rpc.py's parse_exercises_csv().

Verified against a REAL exported exercises.csv from a real Cronometer
account (15 real rows spanning 2026-07-15 to 2026-07-21, provided
directly rather than reconstructed from DevTools, since the export
endpoint's Content-Disposition: attachment header didn't surface a body
in the browser's Network panel). This superseded an earlier version of
this parser/these tests that only had column names corroborated via two
independent published reverse-engineerings of the same export API (still
correct on Day/Exercise/Minutes/Calories Burned) but had NOT confirmed
the sign of Calories Burned -- the real file caught that it's stored
NEGATIVE in the export, which the parser now correctly negates back to
positive. Exactly the kind of thing this project's tenets require
verifying rather than assuming."""
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import pytest
from integrations.cronometer_rpc import parse_exercises_csv  # noqa: E402

REAL_EXERCISES_CSV = (
    "Day,Group,Exercise,Minutes,Calories Burned\n"
    '2026-07-15,Uncategorized,"Active Energy Balance (Apple Health)",0.0,-402.21\n'
    '2026-07-15,Uncategorized,"Traditional Strength Training (Apple Health)",57.66853808363279,-243.53\n'
    '2026-07-16,Uncategorized,"Traditional Strength Training (Apple Health)",54.73635186751684,-248.57\n'
    '2026-07-16,Uncategorized,"Active Energy Balance (Apple Health)",0.0,-272.64\n'
    '2026-07-16,Uncategorized,"Martial Arts",50.0,-245.07\n'
    '2026-07-17,Uncategorized,"Active Energy Balance (Apple Health)",0.0,-519.75\n'
    '2026-07-18,Uncategorized,"Resistance Training",50.0,-210.00\n'
    '2026-07-18,Uncategorized,"Traditional Strength Training (Apple Health)",53.165133800109224,-193.26\n'
    '2026-07-18,Uncategorized,"Active Energy Balance (Apple Health)",0.0,-53.20\n'
    '2026-07-18,Uncategorized,"Martial Arts",60.0,-284.26\n'
    '2026-07-19,Uncategorized,"Active Energy Balance (Apple Health)",0.0,-56.86\n'
    '2026-07-20,Uncategorized,"Active Energy Balance (Apple Health)",0.0,-334.04\n'
    '2026-07-21,Uncategorized,"Active Energy Balance (Apple Health)",0.0,-66.08\n'
    '2026-07-21,Uncategorized,"Traditional Strength Training (Apple Health)",54.54991546670596,-261.54\n'
    '2026-07-21,Uncategorized,"Custom Exercise",15.0,-75.00\n'
)


class TestParseExercisesCsvAgainstRealFile:
    def test_parses_all_15_real_rows(self):
        rows = parse_exercises_csv(REAL_EXERCISES_CSV)
        assert len(rows) == 15

    def test_calories_burned_negated_to_positive(self):
        """The real file stores this NEGATIVE (a burn delta) -- the
        parser must return it as positive, matching
        ExerciseLogContract's and add_exercise()'s convention. Getting
        this sign wrong would have silently stored every synced entry
        with negative calories."""
        rows = parse_exercises_csv(REAL_EXERCISES_CSV)
        assert all(r["calories_burned"] > 0 for r in rows if r["calories_burned"] != 0)

    def test_custom_exercise_row_matches_known_add_exercise_capture(self):
        """This exact row (Custom Exercise, 15 min, 75 kcal, 2026-07-21)
        is the SAME real diary entry verified byte-for-byte in
        TestAddExercisePayloadMatchesRealCaptures::
        test_matches_real_capture_custom_activity_15min_75kcal --
        cross-checks the CSV read-side against the GWT-RPC write-side
        for the exact same real entry."""
        rows = parse_exercises_csv(REAL_EXERCISES_CSV)
        custom = next(r for r in rows if r["activity_name"] == "Custom Exercise")
        assert custom["date"] == "2026-07-21"
        assert custom["duration_minutes"] == 15.0
        assert custom["calories_burned"] == 75.0

    def test_apple_health_synced_entries_have_zero_duration(self):
        """Real-world quirk confirmed in the actual file: 'Active Energy
        Balance (Apple Health)' entries always have Minutes=0.0 (a
        passive/background calorie category, not a timed activity) --
        the parser must not choke on or misinterpret a zero duration."""
        rows = parse_exercises_csv(REAL_EXERCISES_CSV)
        balance_rows = [r for r in rows if r["activity_name"] == "Active Energy Balance (Apple Health)"]
        assert len(balance_rows) == 7
        assert all(r["duration_minutes"] == 0.0 for r in balance_rows)
        assert all(r["calories_burned"] > 0 for r in balance_rows)

    def test_group_column_preserved_but_not_required(self):
        """The real file has an extra 'Group' column (always
        'Uncategorized' in this sample) not part of the normalized
        fields -- confirms the parser tolerates and preserves extra
        columns rather than requiring an exact column set."""
        rows = parse_exercises_csv(REAL_EXERCISES_CSV)
        assert rows[0]["Group"] == "Uncategorized"

    def test_all_dates_are_real_sane_dates_in_expected_range(self):
        rows = parse_exercises_csv(REAL_EXERCISES_CSV)
        dates = {r["date"] for r in rows}
        assert dates == {"2026-07-15", "2026-07-16", "2026-07-17", "2026-07-18", "2026-07-19", "2026-07-20", "2026-07-21"}


class TestParseExercisesCsvValidation:
    def test_missing_required_column_raises_loudly(self):
        csv_text = "Date,Activity,Duration\n2026-07-21,Running,30\n"
        with pytest.raises(ValueError, match="missing expected column"):
            parse_exercises_csv(csv_text)

    def test_missing_calories_column_raises_loudly(self):
        csv_text = "Day,Exercise,Minutes,SomeUnknownColumn\n2026-07-21,Running,30,999\n"
        with pytest.raises(ValueError, match="no recognizable calories-burned column"):
            parse_exercises_csv(csv_text)

    def test_empty_csv_returns_empty_list(self):
        assert parse_exercises_csv("") == []

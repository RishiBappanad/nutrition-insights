"""
Unit tests verifying integrations/cronometer_rpc.py's log_diary_entry()
payload construction against REAL captured Network-tab requests from a
live, authenticated Cronometer session (captured 2026-07-20). This is
the strongest verification available for a reverse-engineered write
endpoint without live-hitting a real account — byte-for-byte comparison
against ground truth, not just "does it look plausible."
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from integrations.cronometer_rpc import CronometerRPCClient, GWT_PERMUTATION  # noqa: E402


def _make_client(nonce: str, user_id: str) -> CronometerRPCClient:
    """Build a client already in the 'logged in' state (nonce + user_id
    set directly) without going through login() — this test is purely
    about payload construction, not the auth flow."""
    client = CronometerRPCClient.__new__(CronometerRPCClient)
    client.session = MagicMock()
    client.nonce = nonce
    client.user_id = user_id
    return client


class TestLogDiaryEntryPayloadMatchesRealCaptures:
    """Each test reconstructs the scenario from one exact real capture
    and asserts log_diary_entry() produces a wire payload matching it on
    every field EXCEPT the packed value's low-16-bit local entry id — a
    server-assigned/incrementing per-day sequence number confirmed to
    vary run-to-run in the real captures themselves (3, 4, 1, 2, 11 across
    5 captures with otherwise-identical meal/quantity/food), which this
    client correctly sends as 0 (a value the server can safely renumber).
    Every OTHER field (meal type via >>16, quantity*100, food id, measure
    id) is still verified byte-for-byte against the real capture."""

    def _assert_matches_capture(self, sent_body: str, nonce: str, user_id: str, day: str,
                                 meal_packed_high_bits: int, qty_wire: int, food_id: int, measure_id: int):
        prefix = (
            "7|0|12|https://cronometer.com/cronometer/|"
            f"{GWT_PERMUTATION}|com.cronometer.shared.rpc.CronometerService|updateDiary|"
            "java.lang.String/2004016611|I|java.util.List|"
            f"{nonce}|"
            "java.util.Collections$SingletonList/1586180994|"
            "com.cronometer.shared.entries.changes.AddEntryChange/3949104564|"
            "com.cronometer.shared.entries.models.Serving/2553599101|"
            "com.cronometer.shared.entries.models.Day/782579793|"
            f"1|2|3|4|3|5|6|7|8|{user_id}|9|10|1|1|11|12|{day}|"
            "1|1|0|"
        )
        suffix = f"|0|0|{qty_wire}|{food_id}|A|{measure_id}|0|0|"
        assert sent_body.startswith(prefix), f"prefix mismatch:\n{sent_body!r}\nvs\n{prefix!r}"
        assert sent_body.endswith(suffix), f"suffix mismatch:\n{sent_body!r}\nvs\n{suffix!r}"
        packed_value = int(sent_body[len(prefix):-len(suffix)])
        assert packed_value >> 16 == meal_packed_high_bits, (
            f"meal type mismatch: got {packed_value >> 16}, expected {meal_packed_high_bits}"
        )

    def test_breakfast_qty1(self):
        client = _make_client("bb530db5e399ec7750a46de5ff21ada6", "16142312")
        fake_response = MagicMock(text="//OK[1]")
        client.session.post.return_value = fake_response

        client.log_diary_entry(food_id=38403202, measure_id=114508961, meal="breakfast", quantity=1, day="2026-07-20")

        sent_body = client.session.post.call_args.kwargs["data"]
        self._assert_matches_capture(
            sent_body, "bb530db5e399ec7750a46de5ff21ada6", "16142312", "20|7|2026",
            meal_packed_high_bits=1, qty_wire=100, food_id=38403202, measure_id=114508961,
        )

    def test_breakfast_qty2(self):
        client = _make_client("bb530db5e399ec7750a46de5ff21ada6", "16142312")
        fake_response = MagicMock(text="//OK[1]")
        client.session.post.return_value = fake_response

        client.log_diary_entry(food_id=38403202, measure_id=114508961, meal="breakfast", quantity=2, day="2026-07-20")

        sent_body = client.session.post.call_args.kwargs["data"]
        self._assert_matches_capture(
            sent_body, "bb530db5e399ec7750a46de5ff21ada6", "16142312", "20|7|2026",
            meal_packed_high_bits=1, qty_wire=200, food_id=38403202, measure_id=114508961,
        )

    def test_dinner_qty1(self):
        client = _make_client("bb530db5e399ec7750a46de5ff21ada6", "16142312")
        fake_response = MagicMock(text="//OK[1]")
        client.session.post.return_value = fake_response

        client.log_diary_entry(food_id=38403202, measure_id=114508961, meal="dinner", quantity=1, day="2026-07-20")

        sent_body = client.session.post.call_args.kwargs["data"]
        self._assert_matches_capture(
            sent_body, "bb530db5e399ec7750a46de5ff21ada6", "16142312", "20|7|2026",
            meal_packed_high_bits=3, qty_wire=100, food_id=38403202, measure_id=114508961,
        )

    def test_lunch_qty3_donuts(self):
        """The very first real capture -- 3 donuts, lunch."""
        client = _make_client("3c730481d1914c141937ca799ea93668", "16142312")
        fake_response = MagicMock(text="//OK[1]")
        client.session.post.return_value = fake_response

        client.log_diary_entry(food_id=452240, measure_id=1006930, meal="lunch", quantity=3, day="2026-07-20")

        sent_body = client.session.post.call_args.kwargs["data"]
        self._assert_matches_capture(
            sent_body, "3c730481d1914c141937ca799ea93668", "16142312", "20|7|2026",
            meal_packed_high_bits=2, qty_wire=300, food_id=452240, measure_id=1006930,
        )

    def test_snack_qty1(self):
        client = _make_client("bb530db5e399ec7750a46de5ff21ada6", "16142312")
        fake_response = MagicMock(text="//OK[1]")
        client.session.post.return_value = fake_response

        client.log_diary_entry(food_id=38403202, measure_id=114508961, meal="snack", quantity=1, day="2026-07-20")

        sent_body = client.session.post.call_args.kwargs["data"]
        self._assert_matches_capture(
            sent_body, "bb530db5e399ec7750a46de5ff21ada6", "16142312", "20|7|2026",
            meal_packed_high_bits=4, qty_wire=100, food_id=38403202, measure_id=114508961,
        )

    def test_recipe_logging_uses_identical_encoding_to_plain_food(self):
        """Real capture: logging a recipe (id 77317513, measure 278341204)
        to the diary -- confirmed identical encoding to a plain food,
        this is the concrete evidence that recipe logging needs zero new
        code beyond what a plain food log already does."""
        client = _make_client("99a8fffb0d8979045aa9200cdf12bd4a", "16142312")
        fake_response = MagicMock(text="//OK[1]")
        client.session.post.return_value = fake_response

        client.log_diary_entry(food_id=77317513, measure_id=278341204, meal="breakfast", quantity=1, day="2026-07-20")

        sent_body = client.session.post.call_args.kwargs["data"]
        self._assert_matches_capture(
            sent_body, "99a8fffb0d8979045aa9200cdf12bd4a", "16142312", "20|7|2026",
            meal_packed_high_bits=1, qty_wire=100, food_id=77317513, measure_id=278341204,
        )


class TestLogDiaryEntryValidation:
    def test_invalid_meal_rejected(self):
        client = _make_client("nonce", "16142312")
        import pytest
        with pytest.raises(ValueError, match="meal must be one of"):
            client.log_diary_entry(food_id=1, measure_id=1, meal="brunch", quantity=1)

    def test_zero_quantity_rejected(self):
        client = _make_client("nonce", "16142312")
        import pytest
        with pytest.raises(ValueError, match="quantity must be positive"):
            client.log_diary_entry(food_id=1, measure_id=1, meal="breakfast", quantity=0)

    def test_negative_quantity_rejected(self):
        client = _make_client("nonce", "16142312")
        import pytest
        with pytest.raises(ValueError, match="quantity must be positive"):
            client.log_diary_entry(food_id=1, measure_id=1, meal="breakfast", quantity=-1)

    def test_meal_case_insensitive(self):
        client = _make_client("nonce", "16142312")
        fake_response = MagicMock(text="//OK[1]")
        client.session.post.return_value = fake_response
        client.log_diary_entry(food_id=1, measure_id=1, meal="BREAKFAST", quantity=1, day="2026-01-01")
        sent_body = client.session.post.call_args.kwargs["data"]
        assert "|65536|" in sent_body  # 1 << 16 | 0

    def test_not_logged_in_raises(self):
        import pytest
        client = CronometerRPCClient.__new__(CronometerRPCClient)
        client.nonce = None
        client.user_id = None
        with pytest.raises(ValueError, match="must be logged in"):
            client.log_diary_entry(food_id=1, measure_id=1, meal="breakfast", quantity=1)

    def test_quantity_fractional_rounds_to_nearest_wire_value(self):
        """0.5 servings -> wire value 50 (0.5 * 100), matching the
        confirmed *100 encoding."""
        client = _make_client("nonce", "16142312")
        fake_response = MagicMock(text="//OK[1]")
        client.session.post.return_value = fake_response
        client.log_diary_entry(food_id=1, measure_id=1, meal="lunch", quantity=0.5, day="2026-01-01")
        sent_body = client.session.post.call_args.kwargs["data"]
        assert "|50|1|A|1|0|0|" in sent_body

    def test_returns_true_on_ok_response(self):
        client = _make_client("nonce", "16142312")
        fake_response = MagicMock(text="//OK[1,2,3]")
        client.session.post.return_value = fake_response
        assert client.log_diary_entry(food_id=1, measure_id=1, meal="breakfast", quantity=1) is True

    def test_returns_false_on_non_ok_response(self):
        client = _make_client("nonce", "16142312")
        fake_response = MagicMock(text="//EX[some error]")
        client.session.post.return_value = fake_response
        assert client.log_diary_entry(food_id=1, measure_id=1, meal="breakfast", quantity=1) is False


class TestAddExercisePayloadMatchesRealCaptures:
    """add_exercise() verified against 3 real captured requests: 2 for a
    real catalog activity ("Martial Arts, Moderate", activity_id 1157,
    ONLY duration + calories varied between them), and 1 for a genuinely
    novel CUSTOM activity (activity_id=0, no catalog match) — PLUS
    cross-validated against 14 real historical records from a captured
    getRecentExercises response. The custom-activity capture is what
    confirmed met_coefficient is a per-USER constant, not per-activity
    (it stayed IDENTICAL across all 3 captures despite one being a
    totally different, previously-unseen activity) — see the
    field-by-field breakdown comment above add_exercise() in
    integrations/cronometer_rpc.py for the full reasoning."""

    def _assert_matches_capture(self, sent_body: str, nonce: str, user_id: str, short_id: str,
                                 activity_name: str, activity_id: int, intensity_code: int, met_coefficient: float,
                                 dom: int, month: int, year: int,
                                 duration_minutes: float, calories_burned_negated: str):
        expected = (
            "7|0|10|https://cronometer.com/cronometer/|"
            f"{GWT_PERMUTATION}|com.cronometer.shared.rpc.CronometerService|addExercise|"
            "java.lang.String/2004016611|com.cronometer.shared.exercise.Exercise/2894167537|I|"
            f"{nonce}|"
            "com.cronometer.shared.entries.models.Day/782579793|"
            f"{activity_name}|1|2|3|4|3|5|6|7|8|"
            f"{activity_id}|{intensity_code}|0|{calories_burned_negated}|9|{dom}|{month}|{year}|{short_id}|0|0|"
            f"{duration_minutes}|10|0|1|0|0|{user_id}|{met_coefficient}|{user_id}|"
        )
        assert sent_body == expected, f"payload mismatch:\ngot:      {sent_body!r}\nexpected: {expected!r}"

    def test_matches_real_capture_60min_288_8kcal(self, monkeypatch):
        """Exact reconstruction of the real capture: Martial Arts,
        Moderate, 60 min, 288.8 kcal burned, logged 2026-07-21."""
        monkeypatch.setattr("uuid.uuid4", lambda: type("U", (), {"hex": "A0000"})())
        client = _make_client("6d4f0a0b138047f41a5972560bfdc154", "16142312")
        fake_response = MagicMock(text="//OK[1]")
        client.session.post.return_value = fake_response

        client.add_exercise(
            activity_name="Martial Arts, Moderate", activity_id=1157, met_coefficient=140.99988486671424,
            duration_minutes=60, calories_burned=288.8, day="2026-07-21",
        )

        sent_body = client.session.post.call_args.kwargs["data"]
        self._assert_matches_capture(
            sent_body, "6d4f0a0b138047f41a5972560bfdc154", "16142312", "A0000",
            activity_name="Martial Arts, Moderate", activity_id=1157, intensity_code=50, met_coefficient=140.99988486671424,
            dom=21, month=7, year=2026, duration_minutes=60, calories_burned_negated="-288.8",
        )

    def test_matches_real_capture_40min_192_5kcal(self, monkeypatch):
        """Exact reconstruction of the second real capture: same
        activity/date, ONLY duration (40 vs 60) and calories (192.5 vs
        288.8) differ — confirms those are the two variable fields and
        nothing else shifted."""
        monkeypatch.setattr("uuid.uuid4", lambda: type("U", (), {"hex": "A0000"})())
        client = _make_client("6d4f0a0b138047f41a5972560bfdc154", "16142312")
        fake_response = MagicMock(text="//OK[1]")
        client.session.post.return_value = fake_response

        client.add_exercise(
            activity_name="Martial Arts, Moderate", activity_id=1157, met_coefficient=140.99988486671424,
            duration_minutes=40, calories_burned=192.5, day="2026-07-21",
        )

        sent_body = client.session.post.call_args.kwargs["data"]
        self._assert_matches_capture(
            sent_body, "6d4f0a0b138047f41a5972560bfdc154", "16142312", "A0000",
            activity_name="Martial Arts, Moderate", activity_id=1157, intensity_code=50, met_coefficient=140.99988486671424,
            dom=21, month=7, year=2026, duration_minutes=40, calories_burned_negated="-192.5",
        )

    def test_matches_real_capture_custom_activity_15min_75kcal(self, monkeypatch):
        """Exact reconstruction of the real custom-activity capture:
        "Custom Exercise", 15 min, 75 kcal burned, logged 2026-07-21 --
        activity_id=0 and intensity_code=0 default correctly for a name
        with no catalog match, and met_coefficient still matches the
        SAME value used for a completely different real activity in the
        other two captures (the key confirmation that it's per-user, not
        per-activity)."""
        monkeypatch.setattr("uuid.uuid4", lambda: type("U", (), {"hex": "A0000"})())
        client = _make_client("6d4f0a0b138047f41a5972560bfdc154", "16142312")
        fake_response = MagicMock(text="//OK[1]")
        client.session.post.return_value = fake_response

        client.add_exercise(
            activity_name="Custom Exercise", duration_minutes=15, calories_burned=75, day="2026-07-21",
        )

        sent_body = client.session.post.call_args.kwargs["data"]
        self._assert_matches_capture(
            sent_body, "6d4f0a0b138047f41a5972560bfdc154", "16142312", "A0000",
            activity_name="Custom Exercise", activity_id=0, intensity_code=0, met_coefficient=140.99988486671424,
            dom=21, month=7, year=2026, duration_minutes=15, calories_burned_negated="-75",
        )

    def test_default_activity_id_is_zero_custom(self):
        """activity_id defaults to 0 (custom/no-catalog-match) when not
        explicitly supplied -- callers don't need a prior activity
        lookup for the common case."""
        client = _make_client("nonce", "16142312")
        fake_response = MagicMock(text="//OK[1]")
        client.session.post.return_value = fake_response
        client.add_exercise(activity_name="Running", duration_minutes=30, calories_burned=200)
        sent_body = client.session.post.call_args.kwargs["data"]
        assert "|0|0|0|-200|9|" in sent_body

    def test_default_met_coefficient_used_when_not_supplied(self):
        client = _make_client("nonce", "16142312")
        fake_response = MagicMock(text="//OK[1]")
        client.session.post.return_value = fake_response
        client.add_exercise(activity_name="Running", duration_minutes=30, calories_burned=200)
        sent_body = client.session.post.call_args.kwargs["data"]
        assert f"|{CronometerRPCClient.DEFAULT_MET_COEFFICIENT}|" in sent_body

    def test_negative_calories_burned_rejected(self):
        import pytest
        client = _make_client("nonce", "16142312")
        with pytest.raises(ValueError, match="must not be negative"):
            client.add_exercise(activity_name="Running", duration_minutes=30, calories_burned=-50)

    def test_zero_duration_rejected(self):
        import pytest
        client = _make_client("nonce", "16142312")
        with pytest.raises(ValueError, match="duration_minutes must be positive"):
            client.add_exercise(activity_name="Running", duration_minutes=0, calories_burned=50)

    def test_not_logged_in_raises(self):
        import pytest
        client = CronometerRPCClient.__new__(CronometerRPCClient)
        client.nonce = None
        client.user_id = None
        with pytest.raises(ValueError, match="must be logged in"):
            client.add_exercise(activity_name="Running", duration_minutes=30, calories_burned=200)

    def test_returns_true_on_ok_response(self):
        client = _make_client("nonce", "16142312")
        fake_response = MagicMock(text="//OK[1,2,3]")
        client.session.post.return_value = fake_response
        assert client.add_exercise(activity_name="Running", duration_minutes=30, calories_burned=200) is True

    def test_returns_false_on_non_ok_response(self):
        client = _make_client("nonce", "16142312")
        fake_response = MagicMock(text="//EX[some error]")
        client.session.post.return_value = fake_response
        assert client.add_exercise(activity_name="Running", duration_minutes=30, calories_burned=200) is False


class TestGetRecentExercisesParsesRealCapture:
    """_parse_recent_exercises_response() verified against ONE real
    captured response containing 14 real historical exercise records
    for a real account. Every date, met_coefficient, duration, calories,
    and activity_id below is checked against what the real response
    actually contains -- not a synthetic/simplified fixture."""

    REAL_RESPONSE = (
        '//OK[147.0,16142312,0,0,14,0,0,20.0,4,0,"sAIl8",2026,4,11,3,-65.34446351999999,0,18,1146,2,'
        '151.2,16142312,0,0,6,0,0,20.0,4,0,"tV0Hk",2026,5,13,3,-76.803558216,0,368,1341,2,'
        '144.6,16142312,0,0,65539,0,0,40.0,4,0,"tw43O",2026,5,23,3,-186.19044416000003,0,175,1231,2,'
        '144.6,16142312,0,0,9,0,0,400.0,4,0,"t_OyE",2026,5,28,3,-229.56291119999997,0,85,1176,2,'
        '142.4,16142312,0,0,18,0,0,40.0,4,0,"u9K6_",2026,6,18,3,-103.99231628800001,0,54,1160,2,'
        '146.6,16142312,0,0,13,0,0,60.0,4,0,"vJ4OA",2026,6,23,3,-190.96586073600002,0,140,1213,2,'
        '147.0,16142312,0,0,4,0,0,40.0,4,0,"vMIMs",2026,6,24,3,-141.34471030399996,0,377,1345,2,'
        '147.0,16142312,0,0,14,0,0,60.0,4,0,"vNlDl",2026,6,24,3,-376.1593096799999,0,7,1138,2,'
        '141.5,16142312,0,0,1,0,0,40.0,4,0,"vTS4A",2026,6,27,3,-247.28021471999998,0,323,1320,2,'
        '142.9,16142312,0,0,14,0,0,15.0,4,0,"vVrOH",2026,6,27,3,-34.00579224,0,370,1342,2,'
        '141.8,16142312,0,0,65540,0,0,40.0,4,0,"vuFye",2026,7,4,3,-90.04708383999998,0,319,1319,2,'
        '144.6,16142312,0,0,1,0,0,300.0,4,0,"v0HU6",2026,7,9,3,-507.229254,0,94,1183,2,'
        '151.2,16142312,0,0,6,0,0,50.0,4,0,"wQqyj",2026,7,18,3,-275.44374200000004,0,214,1256,2,'
        '140.99988486671424,16142312,0,0,1,0,5,60.0,4,0,"A",2026,7,21,3,-288.8,0,50,1157,2,'
        '14,1,["java.util.ArrayList/4159755760","com.cronometer.shared.exercise.Exercise/2894167537",'
        '"com.cronometer.shared.entries.models.Day/782579793","{}","Martial Arts, Moderate"],0,7]'
    )

    def _client(self):
        client = CronometerRPCClient.__new__(CronometerRPCClient)
        client.user_id = "16142312"
        return client

    def test_parses_all_14_real_records(self):
        records = self._client()._parse_recent_exercises_response(self.REAL_RESPONSE)
        assert len(records) == 14

    def test_last_record_matches_known_add_exercise_capture(self):
        """The last record corresponds to the SAME real entry verified
        byte-for-byte in TestAddExercisePayloadMatchesRealCaptures --
        cross-checks the read-side parser against the write-side capture
        for the exact same real diary entry."""
        records = self._client()._parse_recent_exercises_response(self.REAL_RESPONSE)
        last = records[-1]
        assert last["activity_id"] == 1157
        assert last["met_coefficient"] == 140.99988486671424
        assert last["duration_minutes"] == 60.0
        assert last["calories_burned"] == 288.8  # positive, unlike the wire's negated value
        assert last["date"] == "2026-07-21"

    def test_all_dates_are_real_sane_calendar_dates(self):
        """Every one of the 14 real records' dates parses to a real
        calendar date within the account's actual usage window (April
        through July 2026) -- a sign the anchor/chunk-width parsing
        didn't drift onto the wrong bytes for any record."""
        records = self._client()._parse_recent_exercises_response(self.REAL_RESPONSE)
        dates = [r["date"] for r in records]
        assert dates == [
            "2026-04-11", "2026-05-13", "2026-05-23", "2026-05-28", "2026-06-18",
            "2026-06-23", "2026-06-24", "2026-06-24", "2026-06-27", "2026-06-27",
            "2026-07-04", "2026-07-09", "2026-07-18", "2026-07-21",
        ]

    def test_calories_burned_is_positive_for_every_record(self):
        """The wire format negates calories_burned (see add_exercise's
        field breakdown) -- this parser must un-negate it consistently,
        not just for the one record checked byte-for-byte above."""
        records = self._client()._parse_recent_exercises_response(self.REAL_RESPONSE)
        assert all(r["calories_burned"] > 0 for r in records)

    def test_not_logged_in_raises(self):
        import pytest
        client = CronometerRPCClient.__new__(CronometerRPCClient)
        client.nonce = None
        client.user_id = None
        with pytest.raises(ValueError, match="must be logged in"):
            client.get_recent_exercises()

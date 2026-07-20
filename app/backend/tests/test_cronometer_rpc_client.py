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

from integrations.cronometer_rpc import CronometerRPCClient  # noqa: E402


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
            "F25561B47C31168F0ED80B768B647985|com.cronometer.shared.rpc.CronometerService|updateDiary|"
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

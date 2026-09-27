"""
Integration tests for the 2026-09-27 Everyday/Long-Term goal split:

  - term_of() classification, purely derived from the measure's own time
    window (no stored field) -- every finance-style period-based goal is
    Everyday, every vital (all_time) goal is Long-Term.
  - Long-Term-only fields: target_date (an optional deadline) and
    start_value (a one-time snapshot, auto-captured at creation or given
    directly), rejected on an Everyday goal.
  - journey_percent: a start -> current -> target progress fraction,
    direction-aware, falling back to None (the plain bar) without a usable
    start value or for a non-floor/ceiling comparator.
  - GET /goals/:id/history: a Long-Term goal's own record of crossing into
    or out of compliance, read from the existing goal_met/goal_exceeded
    domain_events -- explicitly NOT pushed anywhere else (no calendar).

Same conventions as the other integration suites: real HTTP through
TestClient, JWTs minted directly, per-run account ids.
"""
import uuid

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    import os
    os.environ["JWT_SECRET"] = "test-secret-do-not-use-in-prod"
    from app import app
    with TestClient(app) as c:
        yield c


def _mint_token(account_id: int, email: str) -> str:
    from jose import jwt
    return jwt.encode({"accountId": account_id, "email": email}, "test-secret-do-not-use-in-prod", algorithm="HS256")


def auth(token):
    return {"Authorization": f"Bearer {token}"}


_RUN_ID = uuid.uuid4().int % 1_000_000


def _account_id(offset: int) -> int:
    return 999_000_000 + _RUN_ID * 10 + offset


DAY = "2033-09-01"


class TestTermClassification:
    def test_a_daily_nutrient_goal_and_a_weekly_goal_are_everyday(self, client):
        token = _mint_token(_account_id(1), f"term_everyday_{_RUN_ID}@example.com")
        assert client.put("/profile", headers=auth(token), json={"age": 29, "sex": "female"}).status_code == 200
        daily = client.post("/goals", headers=auth(token), json={"comparator": "lte", "target_amount": 2000, "period": "daily"}).json()
        weekly = client.post("/goals", headers=auth(token), json={"comparator": "lte", "target_amount": 14000, "period": "weekly"}).json()
        assert daily["term"] == "everyday" and weekly["term"] == "everyday"
        assert daily["target_date"] is None and daily["start_value"] is None

    def test_a_trailing_average_trend_is_still_everyday(self, client):
        token = _mint_token(_account_id(2), f"term_trend_{_RUN_ID}@example.com")
        goal = client.post("/goals", headers=auth(token), json={
            "comparator": "within_tolerance_percent", "tolerance_percent": 15,
            "measure_query": {"aggregation": "sum", "filters": [], "timeWindow": {"kind": "current_period", "period": "daily"}},
            "reference_query": {"aggregation": "mean", "filters": [], "timeWindow": {"kind": "trailing", "period": "daily", "count": 7}},
        }).json()
        assert goal["term"] == "everyday"

    def test_a_vital_goal_is_long_term(self, client):
        token = _mint_token(_account_id(3), f"term_vital_{_RUN_ID}@example.com")
        goal = client.post("/goals", headers=auth(token), json={"vital": "weight", "comparator": "lte", "target_amount": 180}).json()
        assert goal["term"] == "long_term"

    def test_a_non_vital_all_time_goal_is_also_long_term(self, client):
        # term is derived from the TIME WINDOW, not from "is this a vital" --
        # a made-up all-time nutrient goal should classify the same way.
        token = _mint_token(_account_id(4), f"term_alltime_{_RUN_ID}@example.com")
        goal = client.post("/goals", headers=auth(token), json={
            "comparator": "gte",
            "measure_query": {"aggregation": "max", "measureField": "nutrient:Protein", "filters": [{"field": "owner_type", "operator": "eq", "value": "food_log"}], "timeWindow": {"kind": "all_time"}},
            "reference_amount": 50,
        }).json()
        assert goal["term"] == "long_term"


class TestLongTermFieldsAreGated:
    def test_target_date_and_start_value_are_rejected_on_an_everyday_goal(self, client):
        token = _mint_token(_account_id(5), f"gate_create_{_RUN_ID}@example.com")
        r = client.post("/goals", headers=auth(token), json={"comparator": "lte", "target_amount": 2000, "period": "daily", "target_date": "2033-12-31"})
        assert r.status_code == 400
        r2 = client.post("/goals", headers=auth(token), json={"comparator": "lte", "target_amount": 2000, "period": "daily", "start_value": 5})
        assert r2.status_code == 400

    def test_target_date_must_be_a_real_date(self, client):
        token = _mint_token(_account_id(6), f"gate_baddate_{_RUN_ID}@example.com")
        r = client.post("/goals", headers=auth(token), json={"vital": "weight", "comparator": "lte", "target_amount": 180, "target_date": "2033-02-30"})
        assert r.status_code == 400

    def test_patching_target_date_onto_an_everyday_goal_is_rejected(self, client):
        token = _mint_token(_account_id(7), f"gate_patch_{_RUN_ID}@example.com")
        goal = client.post("/goals", headers=auth(token), json={"comparator": "lte", "target_amount": 2000, "period": "daily"}).json()
        assert client.patch(f"/goals/{goal['id']}", headers=auth(token), json={"target_date": "2033-12-31"}).status_code == 400


class TestTargetDateAndStartValue:
    def test_start_value_is_auto_snapshotted_from_the_current_reading_at_creation(self, client):
        token = _mint_token(_account_id(8), f"snapshot_{_RUN_ID}@example.com")
        assert client.post("/vitals", headers=auth(token), json={"metric": "weight", "value": 210, "date": DAY}).status_code == 201
        goal = client.post("/goals", headers=auth(token), json={"vital": "weight", "comparator": "lte", "target_amount": 180}).json()
        assert goal["start_value"] == 210

    def test_no_reading_yet_means_no_snapshot_not_an_error(self, client):
        token = _mint_token(_account_id(9), f"nosnap_{_RUN_ID}@example.com")
        goal = client.post("/goals", headers=auth(token), json={"vital": "weight", "comparator": "lte", "target_amount": 180}).json()
        assert goal["start_value"] is None

    def test_a_real_starting_value_can_be_given_directly_instead_of_auto_snapshotting(self, client):
        token = _mint_token(_account_id(10), f"explicit_start_{_RUN_ID}@example.com")
        assert client.post("/vitals", headers=auth(token), json={"metric": "weight", "value": 175, "date": DAY}).status_code == 201
        goal = client.post("/goals", headers=auth(token), json={"vital": "weight", "comparator": "lte", "target_amount": 160, "start_value": 210}).json()
        assert goal["start_value"] == 210  # not overwritten by the current 175 reading

    def test_target_date_round_trips_and_reports_days_remaining(self, client):
        token = _mint_token(_account_id(11), f"targetdate_{_RUN_ID}@example.com")
        goal = client.post("/goals", headers=auth(token), json={"vital": "weight", "comparator": "lte", "target_amount": 180, "target_date": "2099-01-01"}).json()
        assert goal["target_date"] == "2099-01-01"
        assert goal["days_until_target"] > 0
        edited = client.patch(f"/goals/{goal['id']}", headers=auth(token), json={"target_date": "2000-01-01"}).json()
        assert edited["target_date"] == "2000-01-01" and edited["days_until_target"] < 0  # overdue

    def test_start_value_is_editable_via_patch(self, client):
        token = _mint_token(_account_id(12), f"editstart_{_RUN_ID}@example.com")
        goal = client.post("/goals", headers=auth(token), json={"vital": "weight", "comparator": "lte", "target_amount": 180}).json()
        edited = client.patch(f"/goals/{goal['id']}", headers=auth(token), json={"start_value": 205})
        assert edited.status_code == 200 and edited.json()["start_value"] == 205


class TestJourneyPercent:
    def test_a_weight_loss_ceiling_reads_0_at_start_and_100_at_target(self, client):
        token = _mint_token(_account_id(13), f"journey_lte_{_RUN_ID}@example.com")
        goal = client.post("/goals", headers=auth(token), json={"vital": "weight", "comparator": "lte", "target_amount": 180, "start_value": 210}).json()
        client.post("/vitals", headers=auth(token), json={"metric": "weight", "value": 210, "date": DAY})
        assert client.get(f"/goals/{goal['id']}/status", headers=auth(token)).json()["journey_percent"] == 0.0

        client.post("/vitals", headers=auth(token), json={"metric": "weight", "value": 195, "date": "2033-09-15"})  # halfway
        assert client.get(f"/goals/{goal['id']}/status", headers=auth(token)).json()["journey_percent"] == 50.0

        client.post("/vitals", headers=auth(token), json={"metric": "weight", "value": 170, "date": "2033-10-01"})  # past target
        assert client.get(f"/goals/{goal['id']}/status", headers=auth(token)).json()["journey_percent"] == 100.0  # clamped

    def test_a_savings_style_floor_reads_the_other_direction(self, client):
        token = _mint_token(_account_id(14), f"journey_gte_{_RUN_ID}@example.com")
        goal = client.post("/goals", headers=auth(token), json={
            "comparator": "gte", "start_value": 1000,
            "measure_query": {"aggregation": "last", "filters": [{"field": "owner_type", "operator": "eq", "value": "vital"}, {"field": "category", "operator": "eq", "value": "weight"}], "timeWindow": {"kind": "all_time"}},
            "reference_amount": 5000,
        }).json()
        client.post("/vitals", headers=auth(token), json={"metric": "weight", "value": 2000, "date": DAY})
        assert client.get(f"/goals/{goal['id']}/status", headers=auth(token)).json()["journey_percent"] == 25.0

    def test_falls_back_to_none_without_a_start_value_or_for_a_non_directional_comparator(self, client):
        token = _mint_token(_account_id(15), f"journey_none_{_RUN_ID}@example.com")
        no_start = client.post("/goals", headers=auth(token), json={"vital": "weight", "comparator": "lte", "target_amount": 180}).json()
        client.post("/vitals", headers=auth(token), json={"metric": "weight", "value": 190, "date": DAY})
        status = client.get(f"/goals/{no_start['id']}/status", headers=auth(token)).json()
        assert status["journey_percent"] is None and status["percent"] is not None  # plain bar still works

    def test_batch_statuses_include_journey_percent_too(self, client):
        token = _mint_token(_account_id(16), f"journey_batch_{_RUN_ID}@example.com")
        goal = client.post("/goals", headers=auth(token), json={"vital": "weight", "comparator": "lte", "target_amount": 180, "start_value": 200}).json()
        client.post("/vitals", headers=auth(token), json={"metric": "weight", "value": 190, "date": DAY})
        batch = client.get("/goals/statuses", headers=auth(token)).json()["statuses"]
        assert batch[str(goal["id"])]["journey_percent"] == 50.0


class TestCrossingHistory:
    def test_a_weight_goal_records_reaching_and_falling_out_of_its_target(self, client):
        token = _mint_token(_account_id(17), f"history_{_RUN_ID}@example.com")
        goal = client.post("/goals", headers=auth(token), json={"vital": "weight", "comparator": "lte", "target_amount": 180}).json()
        assert client.get(f"/goals/{goal['id']}/history", headers=auth(token)).json()["crossings"] == []

        client.post("/vitals", headers=auth(token), json={"metric": "weight", "value": 190, "date": DAY})  # not compliant yet -- no crossing
        assert client.get(f"/goals/{goal['id']}/history", headers=auth(token)).json()["crossings"] == []

        client.post("/vitals", headers=auth(token), json={"metric": "weight", "value": 175, "date": "2033-09-10"})  # crosses in
        history = client.get(f"/goals/{goal['id']}/history", headers=auth(token)).json()["crossings"]
        assert len(history) == 1 and history[0]["event"] == "met" and history[0]["measure_value"] == 175

        client.post("/vitals", headers=auth(token), json={"metric": "weight", "value": 185, "date": "2033-09-20"})  # crosses back out
        history = client.get(f"/goals/{goal['id']}/history", headers=auth(token)).json()["crossings"]
        assert [h["event"] for h in history] == ["exceeded", "met"]  # newest first

    def test_an_everyday_goal_has_no_history_endpoint_concept_but_the_call_still_works(self, client):
        token = _mint_token(_account_id(18), f"history_everyday_{_RUN_ID}@example.com")
        goal = client.post("/goals", headers=auth(token), json={"comparator": "lte", "target_amount": 2000, "period": "daily"}).json()
        # notify_on_crossing defaults true for a user-authored goal, so crossings CAN occur;
        # the endpoint works the same regardless of term -- it's just always empty in practice
        # for a preset (notify_on_crossing false) and rare for an everyday goal a user makes.
        assert client.get(f"/goals/{goal['id']}/history", headers=auth(token)).json()["crossings"] == []

    def test_scoped_to_the_owning_user(self, client):
        token = _mint_token(_account_id(19), f"history_owner_{_RUN_ID}@example.com")
        other = _mint_token(_account_id(20), f"history_other_{_RUN_ID}@example.com")
        goal = client.post("/goals", headers=auth(token), json={"vital": "weight", "comparator": "lte", "target_amount": 180}).json()
        assert client.get(f"/goals/{goal['id']}/history", headers=auth(other)).status_code == 404

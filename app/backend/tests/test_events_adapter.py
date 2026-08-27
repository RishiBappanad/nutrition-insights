"""
Integration tests for the Universal Event Contract adapter
(routers/events.py) — real HTTP calls through TestClient, following the
same conventions as test_recipes_meals_integration.py.

Covers both event_types this adapter exposes (food_entry -> food_log,
exercise_activity -> exercise_log) to prove the design generalizes
across genuinely different underlying tables, not just one.
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
    return 820_000_000 + _RUN_ID * 10 + offset


TEST_DATE = "2032-05-01"


@pytest.fixture(scope="module")
def user_token():
    return _mint_token(_account_id(1), f"events_test_{_RUN_ID}_a@example.com")


@pytest.fixture(scope="module")
def other_user_token():
    return _mint_token(_account_id(2), f"events_test_{_RUN_ID}_b@example.com")


class TestPostEventsLog:
    def test_food_entry_creates_food_log_row(self, client, user_token):
        r = client.post("/events/log", headers=auth(user_token), json={
            "event_type": "food_entry",
            "occurred_at": TEST_DATE,
            "amount": 150,
            "source": "manual",
            "metadata": {
                "food_name": "Adapter Test Oatmeal",
                "meal": "Breakfast",
                "nutrients": {"Protein": {"value": 5, "unit": "G"}},
            },
        })
        assert r.status_code == 200
        assert "id" in r.json()

        # Confirm it's really in food_log via the domain-specific endpoint
        r2 = client.get(f"/food/log?date={TEST_DATE}", headers=auth(user_token))
        entries = [e for e in r2.json()["entries"] if e["food_name"] == "Adapter Test Oatmeal"]
        assert len(entries) == 1
        assert entries[0]["calories"] == 150
        assert entries[0]["nutrients"]["Protein"]["value"] == 5

    def test_food_entry_missing_food_name_rejected(self, client, user_token):
        r = client.post("/events/log", headers=auth(user_token), json={
            "event_type": "food_entry",
            "occurred_at": TEST_DATE,
            "amount": 100,
            "metadata": {},
        })
        assert r.status_code == 400

    def test_exercise_activity_creates_exercise_log_row(self, client, user_token):
        r = client.post("/events/log", headers=auth(user_token), json={
            "event_type": "exercise_activity",
            "occurred_at": TEST_DATE,
            "amount": 300,
            "source": "manual",
            "metadata": {"activity_name": "Adapter Test Run", "duration_minutes": 30},
        })
        assert r.status_code == 200
        assert "id" in r.json()

        r2 = client.get(f"/exercise?date={TEST_DATE}", headers=auth(user_token))
        entries = [e for e in r2.json()["entries"] if e["activity_name"] == "Adapter Test Run"]
        assert len(entries) == 1
        assert entries[0]["calories_burned"] == 300
        assert entries[0]["duration_minutes"] == 30

    def test_exercise_activity_missing_activity_name_rejected(self, client, user_token):
        r = client.post("/events/log", headers=auth(user_token), json={
            "event_type": "exercise_activity",
            "occurred_at": TEST_DATE,
            "amount": 100,
            "metadata": {},
        })
        assert r.status_code == 400

    def test_unknown_event_type_rejected(self, client, user_token):
        r = client.post("/events/log", headers=auth(user_token), json={
            "event_type": "outfit_worn",
            "occurred_at": TEST_DATE,
            "amount": 1,
            "metadata": {},
        })
        assert r.status_code == 400


class TestGetEvents:
    def test_returns_both_event_types_in_range(self, client, user_token):
        r = client.get(f"/events?start={TEST_DATE}&end={TEST_DATE}", headers=auth(user_token))
        assert r.status_code == 200
        body = r.json()
        event_types = {e["event_type"] for e in body["events"]}
        assert "food_entry" in event_types
        assert "exercise_activity" in event_types
        assert body["total"] == len(body["events"])

    def test_food_entry_has_core_event_shape(self, client, user_token):
        r = client.get(f"/events?start={TEST_DATE}&end={TEST_DATE}&event_type=food_entry", headers=auth(user_token))
        events = r.json()["events"]
        entry = next(e for e in events if e["metadata"]["food_name"] == "Adapter Test Oatmeal")
        assert entry["event_type"] == "food_entry"
        assert entry["occurred_at"] == TEST_DATE
        assert entry["amount"] == 150
        assert entry["source"] == "manual"
        assert entry["hidden"] is False
        assert entry["status"] is None
        # Known, tracked gap -- category is not fabricated, always null today
        assert entry["category"] is None
        assert entry["metadata"]["nutrients"]["Protein"]["value"] == 5

    def test_exercise_activity_has_core_event_shape(self, client, user_token):
        r = client.get(f"/events?start={TEST_DATE}&end={TEST_DATE}&event_type=exercise_activity", headers=auth(user_token))
        events = r.json()["events"]
        entry = next(e for e in events if e["metadata"]["activity_name"] == "Adapter Test Run")
        assert entry["event_type"] == "exercise_activity"
        assert entry["amount"] == 300
        assert entry["category"] is None
        assert entry["metadata"]["duration_minutes"] == 30

    def test_event_type_filter_excludes_other_type(self, client, user_token):
        r = client.get(f"/events?start={TEST_DATE}&end={TEST_DATE}&event_type=food_entry", headers=auth(user_token))
        event_types = {e["event_type"] for e in r.json()["events"]}
        assert event_types == {"food_entry"}

    def test_source_filter(self, client, user_token):
        r = client.get(f"/events?start={TEST_DATE}&end={TEST_DATE}&source=manual", headers=auth(user_token))
        assert all(e["source"] == "manual" for e in r.json()["events"])

    def test_date_range_excludes_outside_range(self, client, user_token):
        r = client.get("/events?start=2020-01-01&end=2020-01-02", headers=auth(user_token))
        assert r.json()["events"] == []

    def test_invalid_event_type_filter_rejected(self, client, user_token):
        r = client.get(f"/events?start={TEST_DATE}&end={TEST_DATE}&event_type=bogus", headers=auth(user_token))
        assert r.status_code == 400

    def test_scoped_to_owner(self, client, user_token, other_user_token):
        r = client.get(f"/events?start={TEST_DATE}&end={TEST_DATE}", headers=auth(other_user_token))
        names = [e["metadata"].get("food_name") for e in r.json()["events"]]
        assert "Adapter Test Oatmeal" not in names


class TestGetAggregations:
    def test_by_event_type(self, client, user_token):
        r = client.get(f"/aggregations/by_event_type?start={TEST_DATE}&end={TEST_DATE}", headers=auth(user_token))
        assert r.status_code == 200
        data = {row["event_type"]: row["total_amount"] for row in r.json()["data"]}
        assert data.get("food_entry", 0) >= 150
        assert data.get("exercise_activity", 0) >= 300

    def test_by_source(self, client, user_token):
        r = client.get(f"/aggregations/by_source?start={TEST_DATE}&end={TEST_DATE}", headers=auth(user_token))
        assert r.status_code == 200
        sources = {row["source"] for row in r.json()["data"]}
        assert "manual" in sources

    def test_by_category_degenerates_to_uncategorized(self, client, user_token):
        """Known, tracked gap (see routers/events.py's module docstring):
        category isn't populated yet, so every event falls into a single
        honest 'uncategorized' bucket rather than a fabricated breakdown."""
        r = client.get(f"/aggregations/by_category?start={TEST_DATE}&end={TEST_DATE}", headers=auth(user_token))
        assert r.status_code == 200
        data = r.json()["data"]
        assert len(data) == 1
        assert data[0]["category"] == "uncategorized"

    def test_unknown_agg_type_rejected(self, client, user_token):
        r = client.get(f"/aggregations/by_nonsense?start={TEST_DATE}&end={TEST_DATE}", headers=auth(user_token))
        assert r.status_code == 400

    def test_unit_is_kcal_for_both_event_types(self, client, user_token):
        r = client.get(f"/aggregations/by_event_type?start={TEST_DATE}&end={TEST_DATE}", headers=auth(user_token))
        assert all(row["unit"] == "kcal" for row in r.json()["data"])

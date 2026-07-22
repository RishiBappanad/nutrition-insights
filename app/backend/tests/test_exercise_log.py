"""Manual exercise/activity log tests (POST/GET/PATCH/DELETE /exercise).
Cronometer two-way sync for this domain is a separate, not-yet-built
follow-up (needs real Cronometer exercise-endpoint captures, same
process used for the food diary sync) -- these tests cover the
independently-shippable manual-entry half only. Follows the same
per-file fixture/helper conventions as test_recipes_meals_integration.py
(no shared conftest.py in this project)."""
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


TEST_DATE = "2026-07-21"


@pytest.fixture(scope="module")
def user_token():
    return _mint_token(_account_id(1), f"exercise_test_{_RUN_ID}_a@example.com")


@pytest.fixture(scope="module")
def other_user_token():
    return _mint_token(_account_id(2), f"exercise_test_{_RUN_ID}_b@example.com")


class TestExerciseLog:
    def test_log_and_list_activity(self, client, user_token):
        r = client.post("/exercise", headers=auth(user_token), json={
            "date": TEST_DATE, "activity_name": "Running", "duration_minutes": 30, "calories_burned": 300,
        })
        assert r.status_code == 200
        entry_id = r.json()["id"]

        r2 = client.get(f"/exercise?date={TEST_DATE}", headers=auth(user_token))
        assert r2.status_code == 200
        entries = r2.json()["entries"]
        entry = next(e for e in entries if e["id"] == entry_id)
        assert entry["activity_name"] == "Running"
        assert entry["duration_minutes"] == 30
        assert entry["calories_burned"] == 300
        assert entry["source"] == "manual"

    def test_total_calories_burned_sums_all_entries_for_date(self, client, user_token):
        client.post("/exercise", headers=auth(user_token), json={
            "date": TEST_DATE, "activity_name": "Cycling", "calories_burned": 200,
        })
        client.post("/exercise", headers=auth(user_token), json={
            "date": TEST_DATE, "activity_name": "Yoga", "calories_burned": 100,
        })
        r = client.get(f"/exercise?date={TEST_DATE}", headers=auth(user_token))
        assert r.json()["total_calories_burned"] >= 300  # >= since other tests may share this date

    def test_duration_and_notes_are_optional(self, client, user_token):
        r = client.post("/exercise", headers=auth(user_token), json={
            "date": TEST_DATE, "activity_name": "Walk", "calories_burned": 50,
        })
        assert r.status_code == 200
        entry_id = r.json()["id"]
        r2 = client.get(f"/exercise?date={TEST_DATE}", headers=auth(user_token))
        entry = next(e for e in r2.json()["entries"] if e["id"] == entry_id)
        assert entry["duration_minutes"] is None
        assert entry["notes"] is None

    def test_update_exercise_entry(self, client, user_token):
        r = client.post("/exercise", headers=auth(user_token), json={
            "date": TEST_DATE, "activity_name": "Swimming", "calories_burned": 250,
        })
        entry_id = r.json()["id"]

        r2 = client.patch(f"/exercise/{entry_id}", headers=auth(user_token), json={"calories_burned": 275})
        assert r2.status_code == 200

        r3 = client.get(f"/exercise?date={TEST_DATE}", headers=auth(user_token))
        entry = next(e for e in r3.json()["entries"] if e["id"] == entry_id)
        assert entry["calories_burned"] == 275
        assert entry["activity_name"] == "Swimming"  # untouched field preserved

    def test_update_unknown_entry_404s(self, client, user_token):
        r = client.patch("/exercise/999999999", headers=auth(user_token), json={"calories_burned": 100})
        assert r.status_code == 404

    def test_delete_exercise_entry(self, client, user_token):
        r = client.post("/exercise", headers=auth(user_token), json={
            "date": TEST_DATE, "activity_name": "Rowing", "calories_burned": 150,
        })
        entry_id = r.json()["id"]

        r2 = client.delete(f"/exercise/{entry_id}", headers=auth(user_token))
        assert r2.status_code == 200

        r3 = client.get(f"/exercise?date={TEST_DATE}", headers=auth(user_token))
        assert entry_id not in [e["id"] for e in r3.json()["entries"]]

    def test_delete_unknown_entry_404s(self, client, user_token):
        r = client.delete("/exercise/999999999", headers=auth(user_token))
        assert r.status_code == 404

    def test_exercise_log_scoped_to_owner(self, client, user_token, other_user_token):
        client.post("/exercise", headers=auth(user_token), json={
            "date": TEST_DATE, "activity_name": "Owner Only Activity", "calories_burned": 999,
        })
        r = client.get(f"/exercise?date={TEST_DATE}", headers=auth(other_user_token))
        assert not any(e["activity_name"] == "Owner Only Activity" for e in r.json()["entries"])

    def test_update_scoped_to_owner(self, client, user_token, other_user_token):
        r = client.post("/exercise", headers=auth(user_token), json={
            "date": TEST_DATE, "activity_name": "Cross-User Update Target", "calories_burned": 100,
        })
        entry_id = r.json()["id"]
        r2 = client.patch(f"/exercise/{entry_id}", headers=auth(other_user_token), json={"calories_burned": 1})
        assert r2.status_code == 404

    def test_delete_scoped_to_owner(self, client, user_token, other_user_token):
        r = client.post("/exercise", headers=auth(user_token), json={
            "date": TEST_DATE, "activity_name": "Cross-User Delete Target", "calories_burned": 100,
        })
        entry_id = r.json()["id"]
        r2 = client.delete(f"/exercise/{entry_id}", headers=auth(other_user_token))
        assert r2.status_code == 404

        r3 = client.get(f"/exercise?date={TEST_DATE}", headers=auth(user_token))
        assert entry_id in [e["id"] for e in r3.json()["entries"]]  # still there, other user couldn't delete it

    def test_exercise_requires_auth(self, client):
        r = client.get(f"/exercise?date={TEST_DATE}")
        assert r.status_code in (401, 403)


class TestExerciseContractDedupe:
    """log_exercise_entry() (food_entry_contract.py) dedupes on
    (user_id, source, source_id) when source_id is present -- the same
    class of bug the food diary sync had before its (still-incomplete)
    sync-pointer work, avoided here from the start for whenever
    Cronometer exercise sync is built. NOT tested via HTTP here since no
    endpoint currently exposes source/source_id (POST /exercise always
    sends source='manual', source_id=None) -- this will get real
    coverage once the sync endpoint exists and can be driven through
    TestClient like every other test in this project (this codebase
    doesn't unit-test async DB functions directly, only through HTTP,
    so a separate asyncio.run() harness here would exercise a different,
    unrepresentative event loop than the one asyncpg's pool was created
    on -- confirmed this fails with a real cross-loop asyncpg error when
    attempted)."""

    def test_manual_entries_never_dedupe(self, client, user_token):
        """source='manual' has no source_id -- logging 'Running' twice
        on purpose must create two separate rows, not silently merge."""
        r1 = client.post("/exercise", headers=auth(user_token), json={
            "date": TEST_DATE, "activity_name": "Running", "calories_burned": 100,
        })
        r2 = client.post("/exercise", headers=auth(user_token), json={
            "date": TEST_DATE, "activity_name": "Running", "calories_burned": 100,
        })
        assert r1.json()["id"] != r2.json()["id"]

"""
Tests for per-user data isolation.
Ensures users can only access their own data.

Architecture note: isolation is enforced at the row level — all users share
one Postgres database, and every data table (daily_nutrition, lift_orm,
food_log) is scoped by a user_id column that's checked on every query.
Identity (registration/login) now lives in trackstack-auth, not here — this
service only verifies JWTs signed with the shared secret and trusts the
accountId claim, so tests mint their own trackstack-auth-shaped tokens
directly rather than registering through a local endpoint.
These tests verify that:
1. Auth is required for all data endpoints
2. Each user only sees their own food log entries
3. Deleting an entry only affects rows owned by the requesting user_id
4. Data reset only clears the current user's rows
"""
import time
import pytest
from fastapi.testclient import TestClient


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def client():
    """Create a test client. Uses TestClient's context manager so the app's
    startup/shutdown lifespan events run on the same event loop TestClient
    uses for requests — required because asyncpg pools are loop-bound and
    will raise InterfaceError if created on a different loop than the one
    used to run queries."""
    import os
    os.environ["JWT_SECRET"] = "test-secret-do-not-use-in-prod"

    from app import app

    with TestClient(app) as c:
        yield c


def _mint_token(account_id: int, email: str) -> str:
    """Simulate a token issued by trackstack-auth: { accountId, email },
    signed with the same shared secret. This service never issues its own
    tokens anymore, so tests construct one directly instead of registering."""
    from jose import jwt
    return jwt.encode({"accountId": account_id, "email": email}, "test-secret-do-not-use-in-prod", algorithm="HS256")


@pytest.fixture(scope="module")
def user_a_token():
    # Large, time-based ids to avoid colliding with real migrated accounts (1-5).
    account_id = 900001 + int(time.time()) % 1000
    return _mint_token(account_id, "isolation_test_user_a_xyz@example.com")


@pytest.fixture(scope="module")
def user_b_token():
    account_id = 950001 + int(time.time()) % 1000
    return _mint_token(account_id, "isolation_test_user_b_xyz@example.com")


def auth(token):
    return {"Authorization": f"Bearer {token}"}


FOOD_ENTRY = {
    "date": "2030-01-01",  # Far future date to avoid collisions with real data
    "meal": "Lunch",
    "food_name": "Test Isolation Banana",
    "calories": 105,
    "protein": 1.3,
    "carbs": 27,
    "fat": 0.4,
    "fiber": 3.1,
}


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestAuthentication:

    def test_unauthenticated_bmr_rejected(self, client):
        r = client.get("/data/bmr")
        assert r.status_code in (401, 403)

    def test_unauthenticated_food_log_rejected(self, client):
        r = client.get("/food/log?date=2026-07-09")
        assert r.status_code in (401, 403)

    def test_unauthenticated_chart_rejected(self, client):
        r = client.get("/data/chart?metrics=")
        assert r.status_code in (401, 403)

    def test_unauthenticated_sync_rejected(self, client):
        r = client.post("/sync/cronometer")
        assert r.status_code in (401, 403)

    def test_fake_token_rejected(self, client):
        r = client.get("/data/bmr", headers={"Authorization": "Bearer fake.token.here"})
        assert r.status_code in (401, 403)

    def test_malformed_auth_header_rejected(self, client):
        r = client.get("/data/bmr", headers={"Authorization": "NotBearer token"})
        assert r.status_code in (401, 403)


class TestFoodLogIsolation:

    def test_user_a_log_is_empty_initially(self, client, user_a_token):
        """User A starts with no food entries for the test date."""
        r = client.get(f"/food/log?date={FOOD_ENTRY['date']}", headers=auth(user_a_token))
        assert r.status_code == 200
        # May have entries from previous test runs — we'll clean up before asserting
        # Reset data to ensure clean state
        client.delete("/data/reset", headers=auth(user_a_token))
        r = client.get(f"/food/log?date={FOOD_ENTRY['date']}", headers=auth(user_a_token))
        assert r.status_code == 200
        assert len(r.json()["entries"]) == 0

    def test_user_a_can_log_food(self, client, user_a_token):
        """User A can log a food entry."""
        r = client.post("/food/log", headers=auth(user_a_token), json=FOOD_ENTRY)
        assert r.status_code == 200

    def test_user_a_sees_own_entry(self, client, user_a_token):
        """User A sees the entry they logged."""
        r = client.get(f"/food/log?date={FOOD_ENTRY['date']}", headers=auth(user_a_token))
        assert r.status_code == 200
        entries = r.json()["entries"]
        assert len(entries) >= 1
        names = [e["food_name"] for e in entries]
        assert FOOD_ENTRY["food_name"] in names

    def test_user_b_does_not_see_user_a_entry(self, client, user_a_token, user_b_token):
        """User B cannot see User A's food entries (row-scoped by user_id in shared Postgres)."""
        # Ensure User B's data is clean for this date
        client.delete("/data/reset", headers=auth(user_b_token))

        # User A has an entry (from previous test)
        r_a = client.get(f"/food/log?date={FOOD_ENTRY['date']}", headers=auth(user_a_token))
        assert len(r_a.json()["entries"]) >= 1

        # User B sees nothing
        r_b = client.get(f"/food/log?date={FOOD_ENTRY['date']}", headers=auth(user_b_token))
        assert r_b.status_code == 200
        assert len(r_b.json()["entries"]) == 0, \
            "User B should NOT see User A's food log entries"

    def test_user_b_delete_does_not_affect_user_a(self, client, user_a_token, user_b_token):
        """
        User B cannot delete User A's entries — the DELETE query is scoped by
        both entry id AND the requesting user_id, so User B can never affect
        a row they don't own even if they guess A's row id.
        """
        # Get User A's entry ID
        r_a = client.get(f"/food/log?date={FOOD_ENTRY['date']}", headers=auth(user_a_token))
        entries = r_a.json()["entries"]
        assert len(entries) >= 1
        entry_id = entries[0]["id"]

        # User B tries to delete User A's entry ID (user_id mismatch -> no rows affected)
        r_del = client.delete(f"/food/log/{entry_id}", headers=auth(user_b_token))
        assert r_del.status_code == 200  # Succeeds but deletes nothing owned by B

        # User A's entry is still there
        r_check = client.get(f"/food/log?date={FOOD_ENTRY['date']}", headers=auth(user_a_token))
        names = [e["food_name"] for e in r_check.json()["entries"]]
        assert FOOD_ENTRY["food_name"] in names, \
            "User A's entry should still exist after User B's delete attempt"


class TestDataReset:

    def test_reset_clears_only_current_user(self, client, user_a_token, user_b_token):
        """Reset only clears the authenticated user's data."""
        test_date = "2030-01-02"
        entry = {**FOOD_ENTRY, "date": test_date, "food_name": "Reset Test Food"}

        # Both users log an entry
        client.post("/food/log", headers=auth(user_a_token), json=entry)
        client.post("/food/log", headers=auth(user_b_token), json=entry)

        # Confirm both have data
        assert len(client.get(f"/food/log?date={test_date}", headers=auth(user_a_token)).json()["entries"]) >= 1
        assert len(client.get(f"/food/log?date={test_date}", headers=auth(user_b_token)).json()["entries"]) >= 1

        # User A resets
        r = client.delete("/data/reset", headers=auth(user_a_token))
        assert r.status_code == 200

        # User A has no data for this date
        r_a = client.get(f"/food/log?date={test_date}", headers=auth(user_a_token))
        assert len(r_a.json()["entries"]) == 0, "User A's data should be cleared"

        # User B's data is untouched
        r_b = client.get(f"/food/log?date={test_date}", headers=auth(user_b_token))
        assert len(r_b.json()["entries"]) >= 1, "User B's data should NOT be affected"

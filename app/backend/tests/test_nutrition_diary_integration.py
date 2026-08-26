"""
Integration tests for the nutrition diary feature: nutrient-complete food
logging, targets (macro + micronutrient), water tracking, diary notes,
pantry (including the atomic consume flow), and profile/DRI-seeding.

Follows the same conventions as test_data_isolation.py: real HTTP calls
through TestClient (not direct service-function calls), trackstack-auth
-shaped JWTs minted directly (no local registration), far-future dates
and unique-per-test-run account ids to avoid colliding with real data.

Run against a disposable Neon branch, not production, per this project's
established testing practice (see trackstack-notes.md).
"""
import time
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


# Unique per test-session run (not per-second, unlike test_data_isolation.py
# — that file's time.time()-based ids have a real collision bug across
# reruns within the same second-modulo window, see nutrition-diary-design.md
# note; this file uses a random UUID-derived id instead so reruns never
# collide with themselves or with each other).
_RUN_ID = uuid.uuid4().int % 1_000_000


def _account_id(offset: int) -> int:
    return 800_000_000 + _RUN_ID * 10 + offset


TEST_DATE = "2031-03-15"  # far-future, dedicated to this test file


@pytest.fixture(scope="module")
def user_token():
    return _mint_token(_account_id(1), f"diary_test_{_RUN_ID}_a@example.com")


@pytest.fixture(scope="module")
def other_user_token():
    return _mint_token(_account_id(2), f"diary_test_{_RUN_ID}_b@example.com")


BANANA_NUTRIENTS = {
    "Sodium, Na": {"value": 1.0, "unit": "mg"},
    "Potassium, K": {"value": 358.0, "unit": "mg"},
    "Vitamin C, total ascorbic acid": {"value": 8.7, "unit": "mg"},
}


# ── Food logging: nutrient-complete storage ─────────────────────────────────

class TestFoodLogNutrients:
    def test_log_with_nutrients_persists_structurally(self, client, user_token):
        entry = {
            "date": TEST_DATE,
            "meal": "Breakfast",
            "food_name": "Diary Test Banana",
            "source": "USDA",
            "calories": 105,
            "protein": 1.3,
            "carbs": 27,
            "fat": 0.4,
            "nutrients": BANANA_NUTRIENTS,
        }
        r = client.post("/food/log", headers=auth(user_token), json=entry)
        assert r.status_code == 200
        assert "id" in r.json()

    def test_get_log_returns_per_entry_nutrients(self, client, user_token):
        r = client.get(f"/food/log?date={TEST_DATE}", headers=auth(user_token))
        assert r.status_code == 200
        body = r.json()
        entries = [e for e in body["entries"] if e["food_name"] == "Diary Test Banana"]
        assert len(entries) == 1
        nutrients = entries[0]["nutrients"]
        assert nutrients["Sodium, Na"] == {"value": 1.0, "unit": "mg"}
        assert nutrients["Potassium, K"]["value"] == 358.0

    def test_get_log_returns_nutrient_totals_aggregated(self, client, user_token):
        r = client.get(f"/food/log?date={TEST_DATE}", headers=auth(user_token))
        totals = r.json()["nutrient_totals"]
        assert totals["Sodium, Na"]["value"] == pytest.approx(1.0)
        assert totals["Potassium, K"]["unit"] == "mg"

    def test_log_without_nutrients_field_still_works(self, client, user_token):
        """Backwards compatibility: an entry with no `nutrients` key at
        all (old client, or a food source with no detailed data) should
        still log successfully with just the 5 macro columns."""
        entry = {"date": TEST_DATE, "meal": "Snack", "food_name": "No-Nutrients Item", "calories": 50}
        r = client.post("/food/log", headers=auth(user_token), json=entry)
        assert r.status_code == 200

    def test_second_log_totals_include_both_entries(self, client, user_token):
        r = client.get(f"/food/log?date={TEST_DATE}", headers=auth(user_token))
        assert r.json()["totals"]["calories"] >= 155  # 105 + 50

    def test_malformed_nutrients_entry_does_not_fail_the_whole_request(self, client, user_token):
        entry = {
            "date": TEST_DATE,
            "meal": "Lunch",
            "food_name": "Malformed Nutrients Item",
            "calories": 10,
            "nutrients": {"Bad Field": "not-a-dict-value", "Sodium, Na": {"value": 2, "unit": "mg"}},
        }
        r = client.post("/food/log", headers=auth(user_token), json=entry)
        assert r.status_code == 200

        r2 = client.get(f"/food/log?date={TEST_DATE}", headers=auth(user_token))
        entry_found = [e for e in r2.json()["entries"] if e["food_name"] == "Malformed Nutrients Item"][0]
        assert entry_found["nutrients"] == {"Sodium, Na": {"value": 2.0, "unit": "mg"}}

    def test_deleting_entry_cascades_nutrients(self, client, user_token):
        entry = {
            "date": TEST_DATE, "meal": "Snack", "food_name": "Cascade Delete Test",
            "calories": 1, "nutrients": {"Iron, Fe": {"value": 1, "unit": "mg"}},
        }
        r = client.post("/food/log", headers=auth(user_token), json=entry)
        entry_id = r.json()["id"]

        r_del = client.delete(f"/food/log/{entry_id}", headers=auth(user_token))
        assert r_del.status_code == 200

        r_check = client.get(f"/food/log?date={TEST_DATE}", headers=auth(user_token))
        names = [e["food_name"] for e in r_check.json()["entries"]]
        assert "Cascade Delete Test" not in names


# ── Profile + DRI seeding ────────────────────────────────────────────────────

class TestProfile:
    def test_get_profile_404_before_any_set(self, client, other_user_token):
        r = client.get("/profile", headers=auth(other_user_token))
        assert r.status_code == 404

    def test_set_profile_valid(self, client, user_token):
        r = client.put("/profile", headers=auth(user_token), json={
            "age": 29, "sex": "female", "height_cm": 165, "weight_kg": 60, "activity_level": "moderate",
        })
        assert r.status_code == 200
        body = r.json()
        assert body["water_target_ml"] == 1420.0  # female default, since no override given
        assert body["dri_targets_seeded"] > 0

    def test_get_profile_after_set(self, client, user_token):
        r = client.get("/profile", headers=auth(user_token))
        assert r.status_code == 200
        assert r.json()["age"] == 29
        assert r.json()["sex"] == "female"

    def test_set_profile_invalid_sex_rejected(self, client, user_token):
        r = client.put("/profile", headers=auth(user_token), json={"age": 30, "sex": "unknown"})
        assert r.status_code == 422

    def test_set_profile_invalid_age_rejected(self, client, user_token):
        r = client.put("/profile", headers=auth(user_token), json={"age": -5, "sex": "male"})
        assert r.status_code == 422

    def test_set_profile_invalid_activity_level_rejected(self, client, user_token):
        r = client.put("/profile", headers=auth(user_token), json={"age": 30, "sex": "male", "activity_level": "superhuman"})
        assert r.status_code == 422

    def test_explicit_water_target_override_respected(self, client, user_token):
        r = client.put("/profile", headers=auth(user_token), json={
            "age": 29, "sex": "female", "water_target_ml": 2000,
        })
        assert r.status_code == 200
        assert r.json()["water_target_ml"] == 2000

    def test_profile_save_seeds_nutrition_targets(self, client, user_token):
        r = client.get("/targets/nutrients", headers=auth(user_token))
        assert r.status_code == 200
        targets = {t["nutrient_name"]: t for t in r.json()["targets"]}
        assert "Iron, Fe" in targets
        assert targets["Iron, Fe"]["daily_target"] == 18  # female, 29 -> premenopausal bracket


# ── Targets: macros ──────────────────────────────────────────────────────────

class TestMacroTargets:
    def test_get_macros_404_before_set(self, client, other_user_token):
        # other_user_token has never set profile OR macros
        r = client.get("/targets/macros", headers=auth(other_user_token))
        assert r.status_code == 404

    def test_set_fixed_macros(self, client, user_token):
        r = client.put("/targets/macros", headers=auth(user_token), json={
            "mode": "fixed", "calorie_target": 2000, "protein_g": 150, "carbs_g": 200, "fat_g": 60,
        })
        assert r.status_code == 200
        assert r.json()["protein_g"] == 150

    def test_get_macros_after_fixed_set(self, client, user_token):
        r = client.get("/targets/macros", headers=auth(user_token))
        assert r.status_code == 200
        assert r.json()["mode"] == "fixed"
        assert r.json()["carbs_g"] == 200

    def test_switch_to_ratio_mode(self, client, user_token):
        r = client.put("/targets/macros", headers=auth(user_token), json={
            "mode": "ratio", "calorie_target": 2400, "protein_pct": 30, "carbs_pct": 40, "fat_pct": 30,
        })
        assert r.status_code == 200
        body = r.json()
        assert body["protein_g"] == pytest.approx(180.0, abs=0.1)  # 2400*0.3/4

    def test_ratio_mode_bad_percentages_rejected(self, client, user_token):
        r = client.put("/targets/macros", headers=auth(user_token), json={
            "mode": "ratio", "calorie_target": 2000, "protein_pct": 50, "carbs_pct": 50, "fat_pct": 50,
        })
        assert r.status_code == 400

    def test_fixed_mode_missing_fields_rejected(self, client, user_token):
        r = client.put("/targets/macros", headers=auth(user_token), json={
            "mode": "fixed", "calorie_target": 2000,
        })
        assert r.status_code == 400


# ── Targets: micronutrients + progress ───────────────────────────────────────

class TestNutrientTargetsAndProgress:
    def test_override_a_nutrient_target(self, client, user_token):
        r = client.put("/targets/nutrients/Iron, Fe", headers=auth(user_token), json={
            "nutrient_name": "Iron, Fe", "daily_target": 22, "is_custom": True,
        })
        assert r.status_code == 200

        r2 = client.get("/targets/nutrients", headers=auth(user_token))
        targets = {t["nutrient_name"]: t for t in r2.json()["targets"]}
        assert targets["Iron, Fe"]["daily_target"] == 22
        assert targets["Iron, Fe"]["is_custom"] is True

    def test_override_unknown_nutrient_404s(self, client, user_token):
        r = client.put("/targets/nutrients/Not A Real Nutrient", headers=auth(user_token), json={
            "nutrient_name": "Not A Real Nutrient", "daily_target": 5,
        })
        assert r.status_code == 404

    def test_progress_reflects_logged_food_vs_target(self, client, user_token):
        # user_token already logged a banana with Potassium 358mg on TEST_DATE
        r = client.get(f"/targets/progress?date={TEST_DATE}", headers=auth(user_token))
        assert r.status_code == 200
        progress = r.json()["progress"]
        assert progress["Potassium, K"]["actual"] >= 358.0
        assert progress["Potassium, K"]["daily_target"] == 2600  # female RDA
        assert progress["Potassium, K"]["percent_of_target"] is not None

    def test_progress_for_day_with_no_entries_shows_zero_actual(self, client, user_token):
        r = client.get("/targets/progress?date=2031-01-01", headers=auth(user_token))
        progress = r.json()["progress"]
        assert progress["Iron, Fe"]["actual"] == 0.0

    def test_progress_requires_auth(self, client):
        r = client.get(f"/targets/progress?date={TEST_DATE}")
        assert r.status_code in (401, 403)


# ── Water tracking ───────────────────────────────────────────────────────────

class TestWaterTracking:
    def test_log_water(self, client, user_token):
        r = client.post("/water/log", headers=auth(user_token), json={"date": TEST_DATE, "amount_ml": 250})
        assert r.status_code == 200
        assert "id" in r.json()

    def test_log_negative_amount_rejected(self, client, user_token):
        r = client.post("/water/log", headers=auth(user_token), json={"date": TEST_DATE, "amount_ml": -50})
        assert r.status_code == 400

    def test_get_water_log_totals_and_target(self, client, user_token):
        client.post("/water/log", headers=auth(user_token), json={"date": TEST_DATE, "amount_ml": 300})
        r = client.get(f"/water/log?date={TEST_DATE}", headers=auth(user_token))
        assert r.status_code == 200
        body = r.json()
        assert body["total_ml"] >= 550  # 250 + 300 from this class's two logs
        assert body["target_ml"] == 2000  # set earlier in TestProfile via explicit override
        assert body["percent_of_target"] is not None

    def test_water_target_none_when_no_profile(self, client, other_user_token):
        r = client.get(f"/water/log?date={TEST_DATE}", headers=auth(other_user_token))
        assert r.status_code == 200
        assert r.json()["target_ml"] is None
        assert r.json()["percent_of_target"] is None

    def test_delete_water_entry_scoped_to_owner(self, client, user_token, other_user_token):
        r = client.post("/water/log", headers=auth(user_token), json={"date": TEST_DATE, "amount_ml": 100})
        entry_id = r.json()["id"]

        # other user cannot delete it
        client.delete(f"/water/log/{entry_id}", headers=auth(other_user_token))
        r_check = client.get(f"/water/log?date={TEST_DATE}", headers=auth(user_token))
        assert entry_id in [e["id"] for e in r_check.json()["entries"]]

        # owner can delete it
        r_del = client.delete(f"/water/log/{entry_id}", headers=auth(user_token))
        assert r_del.status_code == 200
        r_check2 = client.get(f"/water/log?date={TEST_DATE}", headers=auth(user_token))
        assert entry_id not in [e["id"] for e in r_check2.json()["entries"]]


# ── Diary notes ───────────────────────────────────────────────────────────────

class TestDiaryNotes:
    def test_get_note_before_any_set_returns_null_not_404(self, client, other_user_token):
        r = client.get(f"/notes/?date={TEST_DATE}", headers=auth(other_user_token))
        assert r.status_code == 200
        assert r.json()["text"] is None

    def test_set_and_get_note(self, client, user_token):
        r = client.put("/notes/", headers=auth(user_token), json={"date": TEST_DATE, "text": "Felt great today."})
        assert r.status_code == 200

        r2 = client.get(f"/notes/?date={TEST_DATE}", headers=auth(user_token))
        assert r2.json()["text"] == "Felt great today."
        assert r2.json()["attachment_url"] is None

    def test_setting_note_again_overwrites_not_appends(self, client, user_token):
        client.put("/notes/", headers=auth(user_token), json={"date": TEST_DATE, "text": "Updated note."})
        r = client.get(f"/notes/?date={TEST_DATE}", headers=auth(user_token))
        assert r.json()["text"] == "Updated note."

    def test_empty_note_text_rejected(self, client, user_token):
        r = client.put("/notes/", headers=auth(user_token), json={"date": TEST_DATE, "text": "   "})
        assert r.status_code == 400

    def test_delete_note(self, client, user_token):
        r = client.delete(f"/notes/?date={TEST_DATE}", headers=auth(user_token))
        assert r.status_code == 200
        r2 = client.get(f"/notes/?date={TEST_DATE}", headers=auth(user_token))
        assert r2.json()["text"] is None


# ── Pantry ────────────────────────────────────────────────────────────────────

class TestPantryBasics:
    def test_add_countable_item_requires_remaining_servings(self, client, user_token):
        r = client.post("/pantry", headers=auth(user_token), json={
            "food_name": "Crackers Box", "tracking_mode": "countable",
        })
        assert r.status_code == 400

    def test_add_countable_item(self, client, user_token):
        r = client.post("/pantry", headers=auth(user_token), json={
            "food_name": "Crackers Box", "tracking_mode": "countable", "remaining_servings": 18,
            "expiration_date": "2031-06-01",
        })
        assert r.status_code == 200
        assert "id" in r.json()

    def test_add_bulk_item_ignores_remaining_servings(self, client, user_token):
        r = client.post("/pantry", headers=auth(user_token), json={
            "food_name": "Paprika Jar", "tracking_mode": "bulk", "remaining_servings": 999,
        })
        assert r.status_code == 200
        item_id = r.json()["id"]

        r2 = client.get("/pantry", headers=auth(user_token))
        item = [i for i in r2.json()["items"] if i["id"] == item_id][0]
        assert item["remaining_servings"] is None

    def test_add_single_item_forces_remaining_servings_to_one(self, client, user_token):
        r = client.post("/pantry", headers=auth(user_token), json={
            "food_name": "One Apple", "tracking_mode": "single", "remaining_servings": 5,
        })
        item_id = r.json()["id"]
        r2 = client.get("/pantry", headers=auth(user_token))
        item = [i for i in r2.json()["items"] if i["id"] == item_id][0]
        assert item["remaining_servings"] == 1.0

    def test_invalid_tracking_mode_rejected(self, client, user_token):
        r = client.post("/pantry", headers=auth(user_token), json={
            "food_name": "Bad Mode Item", "tracking_mode": "weird_mode",
        })
        assert r.status_code == 400

    def test_list_pantry_scoped_to_user(self, client, user_token, other_user_token):
        r = client.get("/pantry", headers=auth(other_user_token))
        names = [i["food_name"] for i in r.json()["items"]]
        assert "Crackers Box" not in names

    def test_patch_pantry_item(self, client, user_token):
        r = client.post("/pantry", headers=auth(user_token), json={
            "food_name": "Patch Test Item", "tracking_mode": "countable", "remaining_servings": 10,
        })
        item_id = r.json()["id"]

        r2 = client.patch(f"/pantry/{item_id}", headers=auth(user_token), json={"remaining_servings": 3})
        assert r2.status_code == 200

        r3 = client.get("/pantry", headers=auth(user_token))
        item = [i for i in r3.json()["items"] if i["id"] == item_id][0]
        assert item["remaining_servings"] == 3

    def test_delete_pantry_item(self, client, user_token):
        r = client.post("/pantry", headers=auth(user_token), json={
            "food_name": "Delete Test Item", "tracking_mode": "single",
        })
        item_id = r.json()["id"]
        r2 = client.delete(f"/pantry/{item_id}", headers=auth(user_token))
        assert r2.status_code == 200
        r3 = client.get("/pantry", headers=auth(user_token))
        assert item_id not in [i["id"] for i in r3.json()["items"]]

    def test_expiring_endpoint_filters_by_days(self, client, user_token):
        r = client.post("/pantry", headers=auth(user_token), json={
            "food_name": "Expiring Soon Item", "tracking_mode": "single", "expiration_date": "2031-03-20",
        })
        near_id = r.json()["id"]
        r2 = client.post("/pantry", headers=auth(user_token), json={
            "food_name": "Expiring Far Item", "tracking_mode": "single", "expiration_date": "2035-01-01",
        })
        far_id = r2.json()["id"]

        r3 = client.get("/pantry/expiring?days=36500", headers=auth(user_token))  # generous window for far items too
        ids = [i["id"] for i in r3.json()["items"]]
        assert near_id in ids
        assert far_id in ids

        r4 = client.get("/pantry/expiring?days=0", headers=auth(user_token))
        ids_narrow = [i["id"] for i in r4.json()["items"]]
        assert far_id not in ids_narrow


class TestPantryConsumeFlow:
    def test_consume_countable_item_decrements_and_logs_food(self, client, user_token):
        r = client.post("/pantry", headers=auth(user_token), json={
            "food_name": "Consume Test Crackers", "tracking_mode": "countable", "remaining_servings": 5,
            "calories": 60, "protein": 1, "carbs": 10, "fat": 2,
            "nutrients": {"Sodium, Na": {"value": 100, "unit": "mg"}},
        })
        item_id = r.json()["id"]

        r2 = client.post(f"/pantry/{item_id}/consume", headers=auth(user_token), json={
            "servings": 2, "date": TEST_DATE, "meal": "Snack",
        })
        assert r2.status_code == 200
        body = r2.json()
        assert body["pantry_status"] == "decremented"
        assert "food_log_id" in body

        # pantry item shows 3 remaining
        r3 = client.get("/pantry", headers=auth(user_token))
        item = [i for i in r3.json()["items"] if i["id"] == item_id][0]
        assert item["remaining_servings"] == 3

        # food_log has the entry with nutrients SCALED by servings consumed
        # (stored per-serving on the pantry item, factor=2 for 2 servings)
        r4 = client.get(f"/food/log?date={TEST_DATE}", headers=auth(user_token))
        entries = [e for e in r4.json()["entries"] if e["food_name"] == "Consume Test Crackers"]
        assert len(entries) == 1
        assert entries[0]["calories"] == 120
        assert entries[0]["nutrients"]["Sodium, Na"]["value"] == 200

    def test_pantry_item_stores_nutrition_at_add_time(self, client, user_token):
        """Per explicit user request: pantry must carry nutrition
        alongside the food/expiration data it already tracks — added
        via the same POST /pantry contract, not a separate endpoint.
        Fiber is not a macro column on pantry_items — it's carried in
        `nutrients` under "Fiber, total dietary" like every other
        non-macro nutrient, and (since GET /pantry's list response
        doesn't surface nutrients) is only verifiable by consuming the
        item and checking the resulting food_log entry."""
        r = client.post("/pantry", headers=auth(user_token), json={
            "food_name": "Nutrition Carrying Item", "tracking_mode": "single",
            "calories": 250, "protein": 10, "carbs": 30, "fat": 8,
            "nutrients": {"Potassium, K": {"value": 400, "unit": "mg"}, "Fiber, total dietary": {"value": 3, "unit": "G"}},
        })
        item_id = r.json()["id"]
        r2 = client.get("/pantry", headers=auth(user_token))
        item = [i for i in r2.json()["items"] if i["id"] == item_id][0]
        assert item["calories"] == 250
        assert item["protein"] == 10

        r3 = client.post(f"/pantry/{item_id}/consume", headers=auth(user_token), json={
            "servings": 1, "date": TEST_DATE, "meal": "Snack",
        })
        assert r3.status_code == 200
        entries = [e for e in client.get(f"/food/log?date={TEST_DATE}", headers=auth(user_token)).json()["entries"]
                   if e["food_name"] == "Nutrition Carrying Item"]
        assert entries[0]["nutrients"]["Fiber, total dietary"]["value"] == 3

    def test_consume_countable_item_to_exactly_zero_removes_it(self, client, user_token):
        r = client.post("/pantry", headers=auth(user_token), json={
            "food_name": "Last Serving Item", "tracking_mode": "countable", "remaining_servings": 2,
        })
        item_id = r.json()["id"]

        r2 = client.post(f"/pantry/{item_id}/consume", headers=auth(user_token), json={
            "servings": 2, "date": TEST_DATE,
        })
        assert r2.json()["pantry_status"] == "removed"

        r3 = client.get("/pantry", headers=auth(user_token))
        assert item_id not in [i["id"] for i in r3.json()["items"]]

    def test_consume_more_than_remaining_rejected(self, client, user_token):
        r = client.post("/pantry", headers=auth(user_token), json={
            "food_name": "Limited Stock Item", "tracking_mode": "countable", "remaining_servings": 1,
        })
        item_id = r.json()["id"]

        r2 = client.post(f"/pantry/{item_id}/consume", headers=auth(user_token), json={
            "servings": 5, "date": TEST_DATE,
        })
        assert r2.status_code == 400

        # item untouched
        r3 = client.get("/pantry", headers=auth(user_token))
        item = [i for i in r3.json()["items"] if i["id"] == item_id][0]
        assert item["remaining_servings"] == 1

    def test_consume_bulk_item_never_changes_quantity(self, client, user_token):
        r = client.post("/pantry", headers=auth(user_token), json={
            "food_name": "Consume Bulk Spice", "tracking_mode": "bulk",
        })
        item_id = r.json()["id"]

        r2 = client.post(f"/pantry/{item_id}/consume", headers=auth(user_token), json={
            "servings": 1, "date": TEST_DATE,
        })
        assert r2.json()["pantry_status"] == "unchanged"

        r3 = client.get("/pantry", headers=auth(user_token))
        item = [i for i in r3.json()["items"] if i["id"] == item_id][0]
        assert item["remaining_servings"] is None  # still bulk, still no count

    def test_consume_single_item_always_removes_it(self, client, user_token):
        r = client.post("/pantry", headers=auth(user_token), json={
            "food_name": "Consume Single Apple", "tracking_mode": "single",
        })
        item_id = r.json()["id"]

        r2 = client.post(f"/pantry/{item_id}/consume", headers=auth(user_token), json={
            "servings": 1, "date": TEST_DATE,
        })
        assert r2.json()["pantry_status"] == "removed"

        r3 = client.get("/pantry", headers=auth(user_token))
        assert item_id not in [i["id"] for i in r3.json()["items"]]

    def test_finish_bulk_item_removes_it(self, client, user_token):
        r = client.post("/pantry", headers=auth(user_token), json={
            "food_name": "Finish Test Spice", "tracking_mode": "bulk",
        })
        item_id = r.json()["id"]

        r2 = client.post(f"/pantry/{item_id}/finish", headers=auth(user_token))
        assert r2.status_code == 200

        r3 = client.get("/pantry", headers=auth(user_token))
        assert item_id not in [i["id"] for i in r3.json()["items"]]

    def test_finish_unknown_item_404s(self, client, user_token):
        r = client.post("/pantry/999999999/finish", headers=auth(user_token))
        assert r.status_code == 404


class TestPantryRemoveServings:
    """Remove servings from a countable pantry item WITHOUT logging to the
    diary -- for sharing with someone else, spoilage, etc. Per explicit
    user request: keep optionality to remove/decrement without forcing a
    diary log."""

    def test_remove_servings_decrements_without_logging(self, client, user_token):
        r = client.post("/pantry", headers=auth(user_token), json={
            "food_name": "Shared Snack Box", "tracking_mode": "countable", "remaining_servings": 10,
            "calories": 100,
        })
        item_id = r.json()["id"]

        r2 = client.post(f"/pantry/{item_id}/remove", headers=auth(user_token), json={"servings": 4})
        assert r2.status_code == 200
        assert r2.json()["pantry_status"] == "decremented"

        r3 = client.get("/pantry", headers=auth(user_token))
        item = [i for i in r3.json()["items"] if i["id"] == item_id][0]
        assert item["remaining_servings"] == 6

        # nothing logged to the diary
        r4 = client.get(f"/food/log?date={TEST_DATE}", headers=auth(user_token))
        assert not any(e["food_name"] == "Shared Snack Box" for e in r4.json()["entries"])

    def test_remove_servings_to_zero_deletes_item(self, client, user_token):
        r = client.post("/pantry", headers=auth(user_token), json={
            "food_name": "Fully Shared Item", "tracking_mode": "countable", "remaining_servings": 3,
        })
        item_id = r.json()["id"]

        r2 = client.post(f"/pantry/{item_id}/remove", headers=auth(user_token), json={"servings": 3})
        assert r2.json()["pantry_status"] == "removed"

        r3 = client.get("/pantry", headers=auth(user_token))
        assert item_id not in [i["id"] for i in r3.json()["items"]]

    def test_remove_more_than_remaining_rejected(self, client, user_token):
        r = client.post("/pantry", headers=auth(user_token), json={
            "food_name": "Small Stock Item", "tracking_mode": "countable", "remaining_servings": 1,
        })
        item_id = r.json()["id"]

        r2 = client.post(f"/pantry/{item_id}/remove", headers=auth(user_token), json={"servings": 5})
        assert r2.status_code == 400

    def test_remove_rejected_for_single_and_bulk_modes(self, client, user_token):
        r1 = client.post("/pantry", headers=auth(user_token), json={
            "food_name": "Single Mode Item", "tracking_mode": "single",
        })
        r2 = client.post(f"/pantry/{r1.json()['id']}/remove", headers=auth(user_token), json={"servings": 1})
        assert r2.status_code == 400

        r3 = client.post("/pantry", headers=auth(user_token), json={
            "food_name": "Bulk Mode Item", "tracking_mode": "bulk",
        })
        r4 = client.post(f"/pantry/{r3.json()['id']}/remove", headers=auth(user_token), json={"servings": 1})
        assert r4.status_code == 400

    def test_consume_does_not_affect_other_users_pantry(self, client, user_token, other_user_token):
        r = client.post("/pantry", headers=auth(user_token), json={
            "food_name": "Owner Scoped Item", "tracking_mode": "countable", "remaining_servings": 5,
        })
        item_id = r.json()["id"]

        # other user cannot consume an item they don't own
        r2 = client.post(f"/pantry/{item_id}/consume", headers=auth(other_user_token), json={
            "servings": 1, "date": TEST_DATE,
        })
        assert r2.status_code == 404

        # item unaffected
        r3 = client.get("/pantry", headers=auth(user_token))
        item = [i for i in r3.json()["items"] if i["id"] == item_id][0]
        assert item["remaining_servings"] == 5

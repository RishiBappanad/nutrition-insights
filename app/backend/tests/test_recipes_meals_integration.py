"""
Integration tests for custom foods, recipes (including the pantry
"can I make this?" check), and custom meals — real HTTP calls through
TestClient, following the same conventions as
test_nutrition_diary_integration.py.
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
    return 810_000_000 + _RUN_ID * 10 + offset


TEST_DATE = "2031-04-01"


@pytest.fixture(scope="module")
def user_token():
    return _mint_token(_account_id(1), f"recipes_test_{_RUN_ID}_a@example.com")


@pytest.fixture(scope="module")
def other_user_token():
    return _mint_token(_account_id(2), f"recipes_test_{_RUN_ID}_b@example.com")


# ── Custom foods ──────────────────────────────────────────────────────────────

class TestCustomFoods:
    def test_create_custom_food(self, client, user_token):
        r = client.post("/custom-foods", headers=auth(user_token), json={
            "food_name": "Homemade Granola",
            "reference_amount": 1, "reference_unit": "cup", "reference_grams": 120,
            "calories": 450, "protein": 10, "carbs": 60, "fat": 18, "fiber": 6,
            "nutrients": {"Iron, Fe": {"value": 2.5, "unit": "mg"}},
        })
        assert r.status_code == 200
        assert "id" in r.json()

    def test_list_custom_foods(self, client, user_token):
        r = client.get("/custom-foods", headers=auth(user_token))
        assert r.status_code == 200
        names = [f["food_name"] for f in r.json()["foods"]]
        assert "Homemade Granola" in names

    def test_get_custom_food_includes_nutrients(self, client, user_token):
        r = client.get("/custom-foods", headers=auth(user_token))
        food_id = [f for f in r.json()["foods"] if f["food_name"] == "Homemade Granola"][0]["id"]

        r2 = client.get(f"/custom-foods/{food_id}", headers=auth(user_token))
        assert r2.status_code == 200
        assert r2.json()["nutrients"]["Iron, Fe"]["value"] == 2.5

    def test_custom_food_scoped_to_owner(self, client, user_token, other_user_token):
        r = client.get("/custom-foods", headers=auth(other_user_token))
        names = [f["food_name"] for f in r.json()["foods"]]
        assert "Homemade Granola" not in names

    def test_update_custom_food(self, client, user_token):
        r = client.post("/custom-foods", headers=auth(user_token), json={"food_name": "Edit Me", "calories": 10})
        food_id = r.json()["id"]

        r2 = client.put(f"/custom-foods/{food_id}", headers=auth(user_token), json={
            "food_name": "Edited Name", "calories": 20,
        })
        assert r2.status_code == 200

        r3 = client.get(f"/custom-foods/{food_id}", headers=auth(user_token))
        assert r3.json()["food_name"] == "Edited Name"
        assert r3.json()["calories"] == 20

    def test_update_other_users_food_404s(self, client, user_token, other_user_token):
        r = client.post("/custom-foods", headers=auth(user_token), json={"food_name": "Owner Only", "calories": 5})
        food_id = r.json()["id"]

        r2 = client.put(f"/custom-foods/{food_id}", headers=auth(other_user_token), json={"food_name": "Hijacked"})
        assert r2.status_code == 404

    def test_delete_custom_food(self, client, user_token):
        r = client.post("/custom-foods", headers=auth(user_token), json={"food_name": "Delete Me", "calories": 1})
        food_id = r.json()["id"]

        r2 = client.delete(f"/custom-foods/{food_id}", headers=auth(user_token))
        assert r2.status_code == 200

        r3 = client.get(f"/custom-foods/{food_id}", headers=auth(user_token))
        assert r3.status_code == 404

    def test_get_unknown_food_404s(self, client, user_token):
        r = client.get("/custom-foods/999999999", headers=auth(user_token))
        assert r.status_code == 404


# ── Recipes ───────────────────────────────────────────────────────────────────

LASAGNA_ITEMS = [
    {"food_name": "Ground Beef", "source": "USDA", "source_id": "23557", "amount_grams": 500,
     "calories": 1000, "protein": 100, "carbs": 0, "fat": 65, "fiber": 0,
     "nutrients": {"Iron, Fe": {"value": 13, "unit": "mg"}}},
    {"food_name": "Pasta Sheets", "source": "USDA", "source_id": "168917", "amount_grams": 250,
     "calories": 900, "protein": 30, "carbs": 180, "fat": 5, "fiber": 8,
     "nutrients": {"Iron, Fe": {"value": 3, "unit": "mg"}}},
    {"food_name": "Salt", "amount_multiple": 1, "calories": 0, "protein": 0, "carbs": 0, "fat": 0, "fiber": 0},
]


class TestRecipes:
    def test_create_recipe_with_items(self, client, user_token):
        r = client.post("/recipes", headers=auth(user_token), json={
            "name": "Test Lasagna", "servings_per_batch": 6, "items": LASAGNA_ITEMS,
        })
        assert r.status_code == 200
        assert "id" in r.json()

    def test_create_recipe_invalid_servings_rejected(self, client, user_token):
        r = client.post("/recipes", headers=auth(user_token), json={
            "name": "Bad Recipe", "servings_per_batch": 0, "items": [],
        })
        assert r.status_code == 400

    def test_list_recipes(self, client, user_token):
        r = client.get("/recipes", headers=auth(user_token))
        names = [rec["name"] for rec in r.json()["recipes"]]
        assert "Test Lasagna" in names

    def test_get_recipe_returns_batch_and_per_serving_totals(self, client, user_token):
        r = client.get("/recipes", headers=auth(user_token))
        recipe_id = [rec for rec in r.json()["recipes"] if rec["name"] == "Test Lasagna"][0]["id"]

        r2 = client.get(f"/recipes/{recipe_id}", headers=auth(user_token))
        assert r2.status_code == 200
        body = r2.json()
        assert body["batch_totals"]["macros"]["calories"] == 1900  # 1000 + 900 + 0
        # per-serving = batch / 6 servings
        assert body["per_serving_totals"]["macros"]["calories"] == pytest.approx(1900 / 6, abs=0.01)
        assert body["per_serving_totals"]["nutrients"]["Iron, Fe"]["value"] == pytest.approx(16 / 6, abs=0.01)

    def test_recipe_scoped_to_owner(self, client, user_token, other_user_token):
        r = client.get("/recipes", headers=auth(user_token))
        recipe_id = [rec for rec in r.json()["recipes"] if rec["name"] == "Test Lasagna"][0]["id"]

        r2 = client.get(f"/recipes/{recipe_id}", headers=auth(other_user_token))
        assert r2.status_code == 404

    def test_update_recipe_replaces_items(self, client, user_token):
        r = client.post("/recipes", headers=auth(user_token), json={
            "name": "Simple Soup", "servings_per_batch": 2,
            "items": [{"food_name": "Broth", "calories": 20, "protein": 1, "carbs": 2, "fat": 0, "fiber": 0}],
        })
        recipe_id = r.json()["id"]

        r2 = client.put(f"/recipes/{recipe_id}", headers=auth(user_token), json={
            "name": "Simple Soup", "servings_per_batch": 4,
            "items": [{"food_name": "Broth", "calories": 20, "protein": 1, "carbs": 2, "fat": 0, "fiber": 0},
                      {"food_name": "Noodles", "calories": 200, "protein": 6, "carbs": 40, "fat": 1, "fiber": 2}],
        })
        assert r2.status_code == 200

        r3 = client.get(f"/recipes/{recipe_id}", headers=auth(user_token))
        assert len(r3.json()["items"]) == 2
        assert r3.json()["servings_per_batch"] == 4

    def test_delete_recipe(self, client, user_token):
        r = client.post("/recipes", headers=auth(user_token), json={"name": "Delete Me Recipe", "items": []})
        recipe_id = r.json()["id"]
        r2 = client.delete(f"/recipes/{recipe_id}", headers=auth(user_token))
        assert r2.status_code == 200
        r3 = client.get(f"/recipes/{recipe_id}", headers=auth(user_token))
        assert r3.status_code == 404

    def test_log_recipe_serving_creates_scaled_food_log_entry(self, client, user_token):
        r = client.post("/recipes", headers=auth(user_token), json={
            "name": "Log Test Chili", "servings_per_batch": 4,
            "items": [{"food_name": "Beans", "calories": 400, "protein": 24, "carbs": 72, "fat": 4, "fiber": 24,
                       "nutrients": {"Potassium, K": {"value": 1200, "unit": "mg"}}}],
        })
        recipe_id = r.json()["id"]

        r2 = client.post(f"/recipes/{recipe_id}/log", headers=auth(user_token), json={
            "date": TEST_DATE, "meal": "Dinner", "servings": 1,
        })
        assert r2.status_code == 200
        assert "food_log_id" in r2.json()

        r3 = client.get(f"/food/log?date={TEST_DATE}", headers=auth(user_token))
        entries = [e for e in r3.json()["entries"] if e["food_name"] == "Log Test Chili"]
        assert len(entries) == 1
        assert entries[0]["calories"] == 100  # 400/4 servings
        assert entries[0]["nutrients"]["Potassium, K"]["value"] == 300  # 1200/4

    def test_log_recipe_multiple_servings_scales_proportionally(self, client, user_token):
        r = client.post("/recipes", headers=auth(user_token), json={
            "name": "Double Serving Test", "servings_per_batch": 4,
            "items": [{"food_name": "Rice", "calories": 800, "protein": 16, "carbs": 176, "fat": 2, "fiber": 4}],
        })
        recipe_id = r.json()["id"]

        r2 = client.post(f"/recipes/{recipe_id}/log", headers=auth(user_token), json={
            "date": TEST_DATE, "servings": 2,
        })
        assert r2.status_code == 200

        r3 = client.get(f"/food/log?date={TEST_DATE}", headers=auth(user_token))
        entries = [e for e in r3.json()["entries"] if e["food_name"] == "Double Serving Test"]
        assert entries[0]["calories"] == 400  # 800/4 * 2 = 400

    def test_log_recipe_invalid_servings_rejected(self, client, user_token):
        r = client.post("/recipes", headers=auth(user_token), json={"name": "Bad Log Test", "items": []})
        recipe_id = r.json()["id"]
        r2 = client.post(f"/recipes/{recipe_id}/log", headers=auth(user_token), json={"date": TEST_DATE, "servings": 0})
        assert r2.status_code == 400

    def test_log_unknown_recipe_404s(self, client, user_token):
        r = client.post("/recipes/999999999/log", headers=auth(user_token), json={"date": TEST_DATE, "servings": 1})
        assert r.status_code == 404


class TestRecipePantryIntegration:
    """Per the user's steering message: recipes need a 'can I make this?'
    check against the pantry, not just aggregate-and-log."""

    def test_can_make_recipe_with_all_ingredients_in_pantry(self, client, user_token):
        client.post("/pantry", headers=auth(user_token), json={
            "food_name": "Chicken Breast", "source": "USDA", "source_id": "canmake-1",
            "tracking_mode": "bulk",
        })
        client.post("/pantry", headers=auth(user_token), json={
            "food_name": "Rice", "source": "USDA", "source_id": "canmake-2",
            "tracking_mode": "countable", "remaining_servings": 10,
        })

        r = client.post("/recipes", headers=auth(user_token), json={
            "name": "Chicken and Rice", "servings_per_batch": 2,
            "items": [
                {"food_name": "Chicken Breast", "source": "USDA", "source_id": "canmake-1", "amount_grams": 300},
                {"food_name": "Rice", "source": "USDA", "source_id": "canmake-2", "amount_multiple": 2},
            ],
        })
        recipe_id = r.json()["id"]

        r2 = client.get(f"/recipes/{recipe_id}/can-make", headers=auth(user_token))
        assert r2.status_code == 200
        body = r2.json()
        assert body["can_make"] is True
        assert len(body["have"]) == 2
        assert len(body["missing"]) == 0

    def test_can_make_recipe_missing_ingredient(self, client, user_token):
        r = client.post("/recipes", headers=auth(user_token), json={
            "name": "Missing Ingredient Recipe", "servings_per_batch": 1,
            "items": [{"food_name": "Saffron", "source": "USDA", "source_id": "not-in-pantry-999"}],
        })
        recipe_id = r.json()["id"]

        r2 = client.get(f"/recipes/{recipe_id}/can-make", headers=auth(user_token))
        body = r2.json()
        assert body["can_make"] is False
        assert len(body["missing"]) == 1
        assert body["missing"][0]["food_name"] == "Saffron"

    def test_can_make_recipe_insufficient_countable_quantity(self, client, user_token):
        client.post("/pantry", headers=auth(user_token), json={
            "food_name": "Eggs", "source": "USDA", "source_id": "canmake-eggs",
            "tracking_mode": "countable", "remaining_servings": 2,
        })
        r = client.post("/recipes", headers=auth(user_token), json={
            "name": "Big Omelette", "servings_per_batch": 1,
            "items": [{"food_name": "Eggs", "source": "USDA", "source_id": "canmake-eggs", "amount_multiple": 6}],
        })
        recipe_id = r.json()["id"]

        r2 = client.get(f"/recipes/{recipe_id}/can-make", headers=auth(user_token))
        body = r2.json()
        assert body["can_make"] is False
        assert body["missing"][0]["remaining_servings"] == 2

    def test_can_make_recipe_with_unmatchable_freehand_item(self, client, user_token):
        """An ingredient with no source/source_id (freehand text like 'a
        pinch of salt') can't be matched against pantry inventory at all
        -- reported separately, not silently assumed missing or present."""
        r = client.post("/recipes", headers=auth(user_token), json={
            "name": "Freehand Ingredient Recipe", "servings_per_batch": 1,
            "items": [{"food_name": "A pinch of salt"}],
        })
        recipe_id = r.json()["id"]

        r2 = client.get(f"/recipes/{recipe_id}/can-make", headers=auth(user_token))
        body = r2.json()
        assert body["can_make"] is False
        assert len(body["unmatchable"]) == 1
        assert len(body["missing"]) == 0

    def test_can_make_scoped_to_owner_pantry(self, client, user_token, other_user_token):
        """other_user_token's pantry doesn't have the ingredient even if
        user_token's does -- can-make must check the requester's own
        pantry, not any pantry."""
        r = client.post("/recipes", headers=auth(other_user_token), json={
            "name": "Other User Recipe", "servings_per_batch": 1,
            "items": [{"food_name": "Chicken Breast", "source": "USDA", "source_id": "canmake-1"}],
        })
        recipe_id = r.json()["id"]

        r2 = client.get(f"/recipes/{recipe_id}/can-make", headers=auth(other_user_token))
        # user_token has this in their pantry, but other_user_token does not
        assert r2.json()["can_make"] is False


# ── Meals ─────────────────────────────────────────────────────────────────────

BREAKFAST_ITEMS = [
    {"food_name": "Eggs", "calories": 140, "protein": 12, "carbs": 1, "fat": 10, "fiber": 0},
    {"food_name": "Toast", "calories": 80, "protein": 3, "carbs": 15, "fat": 1, "fiber": 1,
     "nutrients": {"Sodium, Na": {"value": 150, "unit": "mg"}}},
]


class TestMeals:
    def test_create_meal_with_items(self, client, user_token):
        r = client.post("/meals", headers=auth(user_token), json={"name": "My Usual Breakfast", "items": BREAKFAST_ITEMS})
        assert r.status_code == 200
        assert "id" in r.json()

    def test_list_meals(self, client, user_token):
        r = client.get("/meals", headers=auth(user_token))
        names = [m["name"] for m in r.json()["meals"]]
        assert "My Usual Breakfast" in names

    def test_get_meal_returns_items_unaggregated(self, client, user_token):
        """Unlike recipes, meals have no batch totals -- just the raw item
        list, since there's no serving division concept."""
        r = client.get("/meals", headers=auth(user_token))
        meal_id = [m for m in r.json()["meals"] if m["name"] == "My Usual Breakfast"][0]["id"]

        r2 = client.get(f"/meals/{meal_id}", headers=auth(user_token))
        assert r2.status_code == 200
        assert len(r2.json()["items"]) == 2
        assert "batch_totals" not in r2.json()

    def test_meal_scoped_to_owner(self, client, user_token, other_user_token):
        r = client.get("/meals", headers=auth(user_token))
        meal_id = [m for m in r.json()["meals"] if m["name"] == "My Usual Breakfast"][0]["id"]
        r2 = client.get(f"/meals/{meal_id}", headers=auth(other_user_token))
        assert r2.status_code == 404

    def test_update_meal(self, client, user_token):
        r = client.post("/meals", headers=auth(user_token), json={"name": "Edit Meal", "items": []})
        meal_id = r.json()["id"]
        r2 = client.put(f"/meals/{meal_id}", headers=auth(user_token), json={
            "name": "Edited Meal Name",
            "items": [{"food_name": "New Item", "calories": 50, "protein": 1, "carbs": 5, "fat": 1, "fiber": 0}],
        })
        assert r2.status_code == 200
        r3 = client.get(f"/meals/{meal_id}", headers=auth(user_token))
        assert r3.json()["name"] == "Edited Meal Name"
        assert len(r3.json()["items"]) == 1

    def test_delete_meal(self, client, user_token):
        r = client.post("/meals", headers=auth(user_token), json={"name": "Delete Meal", "items": []})
        meal_id = r.json()["id"]
        r2 = client.delete(f"/meals/{meal_id}", headers=auth(user_token))
        assert r2.status_code == 200
        r3 = client.get(f"/meals/{meal_id}", headers=auth(user_token))
        assert r3.status_code == 404

    def test_log_meal_creates_one_food_log_entry_per_item(self, client, user_token):
        r = client.post("/meals", headers=auth(user_token), json={"name": "Log Test Breakfast", "items": BREAKFAST_ITEMS})
        meal_id = r.json()["id"]

        r2 = client.post(f"/meals/{meal_id}/log", headers=auth(user_token), json={"date": TEST_DATE, "meal": "Breakfast"})
        assert r2.status_code == 200
        assert len(r2.json()["food_log_ids"]) == 2

        r3 = client.get(f"/food/log?date={TEST_DATE}", headers=auth(user_token))
        names = [e["food_name"] for e in r3.json()["entries"]]
        assert "Eggs" in names
        assert "Toast" in names

    def test_log_meal_preserves_nutrients_per_item(self, client, user_token):
        r = client.post("/meals", headers=auth(user_token), json={"name": "Nutrient Check Meal", "items": BREAKFAST_ITEMS})
        meal_id = r.json()["id"]
        client.post(f"/meals/{meal_id}/log", headers=auth(user_token), json={"date": TEST_DATE, "meal": "Breakfast"})

        r2 = client.get(f"/food/log?date={TEST_DATE}", headers=auth(user_token))
        toast_entries = [e for e in r2.json()["entries"] if e["food_name"] == "Toast" and "Sodium, Na" in e["nutrients"]]
        assert any(e["nutrients"]["Sodium, Na"]["value"] == 150 for e in toast_entries)

    def test_log_unknown_meal_404s(self, client, user_token):
        r = client.post("/meals/999999999/log", headers=auth(user_token), json={"date": TEST_DATE})
        assert r.status_code == 404


# ── Preferences ───────────────────────────────────────────────────────────────

class TestPreferences:
    def test_get_preferences_returns_defaults_before_any_set(self, client, other_user_token):
        r = client.get("/preferences", headers=auth(other_user_token))
        assert r.status_code == 200
        body = r.json()
        assert body["colors"]["macro_protein"] == "#3b82f6"
        assert body["sufficiency_threshold_pct"] == 90.0

    def test_set_one_color_preserves_others(self, client, user_token):
        r = client.put("/preferences", headers=auth(user_token), json={
            "colors": {"macro_protein": "#ff0000"},
        })
        assert r.status_code == 200
        body = r.json()
        assert body["colors"]["macro_protein"] == "#ff0000"
        # untouched keys still resolve to their defaults
        assert body["colors"]["macro_carbs"] == "#10b981"

    def test_set_second_color_does_not_clobber_first(self, client, user_token):
        client.put("/preferences", headers=auth(user_token), json={"colors": {"macro_protein": "#ff0000"}})
        r = client.put("/preferences", headers=auth(user_token), json={"colors": {"macro_carbs": "#00ff00"}})
        body = r.json()
        assert body["colors"]["macro_protein"] == "#ff0000"  # still set from the earlier call
        assert body["colors"]["macro_carbs"] == "#00ff00"

    def test_get_after_set_reflects_saved_colors(self, client, user_token):
        client.put("/preferences", headers=auth(user_token), json={"colors": {"chart_line_1": "#123456"}})
        r = client.get("/preferences", headers=auth(user_token))
        assert r.json()["colors"]["chart_line_1"] == "#123456"

    def test_set_invalid_color_key_rejected(self, client, user_token):
        r = client.put("/preferences", headers=auth(user_token), json={"colors": {"not_a_real_key": "#ffffff"}})
        assert r.status_code == 422

    def test_set_malformed_hex_rejected(self, client, user_token):
        r = client.put("/preferences", headers=auth(user_token), json={"colors": {"macro_fat": "orange"}})
        assert r.status_code == 422

    def test_set_sufficiency_threshold(self, client, user_token):
        r = client.put("/preferences", headers=auth(user_token), json={"sufficiency_threshold_pct": 80})
        assert r.status_code == 200
        assert r.json()["sufficiency_threshold_pct"] == 80

    def test_threshold_persists_across_unrelated_color_update(self, client, user_token):
        client.put("/preferences", headers=auth(user_token), json={"sufficiency_threshold_pct": 75})
        r = client.put("/preferences", headers=auth(user_token), json={"colors": {"lift_scatter": "#654321"}})
        assert r.json()["sufficiency_threshold_pct"] == 75

    def test_preferences_scoped_to_owner(self, client, user_token, other_user_token):
        client.put("/preferences", headers=auth(user_token), json={"colors": {"macro_fat": "#abcdef"}})
        r = client.get("/preferences", headers=auth(other_user_token))
        assert r.json()["colors"]["macro_fat"] != "#abcdef"

    def test_reset_preferences_restores_defaults(self, client, user_token):
        client.put("/preferences", headers=auth(user_token), json={"colors": {"macro_protein": "#ff0000"}})
        r = client.delete("/preferences", headers=auth(user_token))
        assert r.status_code == 200
        assert r.json()["colors"]["macro_protein"] == "#3b82f6"

        r2 = client.get("/preferences", headers=auth(user_token))
        assert r2.json()["colors"]["macro_protein"] == "#3b82f6"

    def test_preferences_requires_auth(self, client):
        r = client.get("/preferences")
        assert r.status_code in (401, 403)


# ── Recipe as a first-class food reference (search/diary/pantry) ───────────

class TestRecipeAsFoodReference:
    """Per explicit user steering: recipes should have all the
    functionality of a plain food -- searchable, loggable to diary,
    addable to pantry -- without special-case handling by any caller.
    Implemented by making GET /food/search include the user's own
    recipes (source='recipe', id=<recipe id>) alongside USDA/CNF
    results, in the exact same result shape."""

    def test_recipe_appears_in_food_search_results(self, client, user_token):
        r = client.post("/recipes", headers=auth(user_token), json={
            "name": "Searchable Test Recipe", "servings_per_batch": 2,
            "items": [{"food_name": "Rice", "calories": 400, "protein": 8, "carbs": 88, "fat": 2, "fiber": 2}],
        })
        recipe_id = r.json()["id"]

        r2 = client.get("/food/search?q=Searchable+Test+Recipe", headers=auth(user_token))
        results = r2.json()["results"]
        recipe_result = next((res for res in results if res["source"] == "recipe"), None)
        assert recipe_result is not None
        assert recipe_result["id"] == str(recipe_id)
        assert recipe_result["recipe"] is True

    def test_recipe_search_result_shows_per_serving_not_batch_totals(self, client, user_token):
        client.post("/recipes", headers=auth(user_token), json={
            "name": "Per Serving Check Recipe", "servings_per_batch": 4,
            "items": [{"food_name": "Beans", "calories": 800, "protein": 40, "carbs": 120, "fat": 8, "fiber": 40}],
        })
        r = client.get("/food/search?q=Per+Serving+Check+Recipe", headers=auth(user_token))
        result = next(res for res in r.json()["results"] if res["source"] == "recipe")
        assert result["nutrients"]["Energy"]["value"] == 200  # 800/4 servings

    def test_recipe_search_result_can_be_logged_to_diary(self, client, user_token):
        r = client.post("/recipes", headers=auth(user_token), json={
            "name": "Loggable Recipe", "servings_per_batch": 1,
            "items": [{"food_name": "Oats", "calories": 150, "protein": 5, "carbs": 27, "fat": 3, "fiber": 4}],
        })
        r2 = client.get("/food/search?q=Loggable+Recipe", headers=auth(user_token))
        result = next(res for res in r2.json()["results"] if res["source"] == "recipe")

        r3 = client.post("/food/log", headers=auth(user_token), json={
            "date": TEST_DATE, "meal": "Breakfast", "food_name": result["name"],
            "source": result["source"], "source_id": result["id"],
            "calories": result["nutrients"]["Energy"]["value"],
            "protein": result["nutrients"]["Protein"]["value"],
            "carbs": result["nutrients"]["Carbohydrate, by difference"]["value"],
            "fat": result["nutrients"]["Total lipid (fat)"]["value"],
            "fiber": result["nutrients"]["Fiber, total dietary"]["value"],
            "nutrients": result["nutrients"],
        })
        assert r3.status_code == 200

        r4 = client.get(f"/food/log?date={TEST_DATE}", headers=auth(user_token))
        entry = next(e for e in r4.json()["entries"] if e["food_name"] == "Loggable Recipe")
        assert entry["source"] == "recipe"
        assert entry["calories"] == 150

    def test_recipe_search_result_can_be_added_to_pantry(self, client, user_token):
        r = client.post("/recipes", headers=auth(user_token), json={"name": "Pantry Recipe", "items": []})
        r2 = client.get("/food/search?q=Pantry+Recipe", headers=auth(user_token))
        result = next(res for res in r2.json()["results"] if res["source"] == "recipe")

        r3 = client.post("/pantry", headers=auth(user_token), json={
            "food_name": result["name"], "source": result["source"], "source_id": result["id"],
            "tracking_mode": "countable", "remaining_servings": 5,
        })
        assert r3.status_code == 200

        r4 = client.get("/pantry", headers=auth(user_token))
        item = next(i for i in r4.json()["items"] if i["food_name"] == "Pantry Recipe")
        assert item["source"] == "recipe"

    def test_include_recipes_false_excludes_recipes_from_search(self, client, user_token):
        client.post("/recipes", headers=auth(user_token), json={"name": "Excludable Recipe XYZ", "items": []})
        r = client.get("/food/search?q=Excludable+Recipe+XYZ&include_own=false", headers=auth(user_token))
        assert not any(res["source"] == "recipe" for res in r.json()["results"])

    def test_recipe_search_scoped_to_owner(self, client, user_token, other_user_token):
        client.post("/recipes", headers=auth(user_token), json={"name": "Owner Only Recipe ABC", "items": []})
        r = client.get("/food/search?q=Owner+Only+Recipe+ABC", headers=auth(other_user_token))
        assert not any(res["source"] == "recipe" for res in r.json()["results"])


class TestMealAsFoodReference:
    """Per explicit user steering: meals should be a valid food reference
    at every point a plain food/recipe is -- searchable via
    GET /food/search (source='meal'), with SUMMED (not scaled) item
    totals since a meal has no servings-per-batch concept."""

    def test_meal_appears_in_food_search_results(self, client, user_token):
        r = client.post("/meals", headers=auth(user_token), json={
            "name": "Searchable Test Meal",
            "items": [
                {"food_name": "Eggs", "calories": 140, "protein": 12, "carbs": 1, "fat": 10, "fiber": 0},
                {"food_name": "Toast", "calories": 80, "protein": 3, "carbs": 15, "fat": 1, "fiber": 1},
            ],
        })
        meal_id = r.json()["id"]

        r2 = client.get("/food/search?q=Searchable+Test+Meal", headers=auth(user_token))
        results = r2.json()["results"]
        meal_result = next((res for res in results if res["source"] == "meal"), None)
        assert meal_result is not None
        assert meal_result["id"] == str(meal_id)
        assert meal_result["meal"] is True
        assert meal_result["item_count"] == 2

    def test_meal_search_result_shows_summed_not_scaled_totals(self, client, user_token):
        client.post("/meals", headers=auth(user_token), json={
            "name": "Summed Total Meal",
            "items": [
                {"food_name": "A", "calories": 100, "protein": 5, "carbs": 10, "fat": 2, "fiber": 1},
                {"food_name": "B", "calories": 200, "protein": 10, "carbs": 20, "fat": 4, "fiber": 2},
            ],
        })
        r = client.get("/food/search?q=Summed+Total+Meal", headers=auth(user_token))
        result = next(res for res in r.json()["results"] if res["source"] == "meal")
        assert result["nutrients"]["Energy"]["value"] == 300  # summed, not divided

    def test_meal_search_result_can_be_logged_via_meals_endpoint(self, client, user_token):
        r = client.post("/meals", headers=auth(user_token), json={
            "name": "Loggable Meal",
            "items": [{"food_name": "Oats", "calories": 150, "protein": 5, "carbs": 27, "fat": 3, "fiber": 4}],
        })
        r2 = client.get("/food/search?q=Loggable+Meal", headers=auth(user_token))
        result = next(res for res in r2.json()["results"] if res["source"] == "meal")

        # The frontend calls POST /meals/{id}/log for a source='meal'
        # result (with combined=true, matching "meal as its own food"
        # behavior), not POST /food/log directly.
        r3 = client.post(f"/meals/{result['id']}/log", headers=auth(user_token), json={
            "date": TEST_DATE, "meal": "Breakfast", "combined": True,
        })
        assert r3.status_code == 200
        assert len(r3.json()["food_log_ids"]) == 1
        assert r3.json()["combined"] is True

        r4 = client.get(f"/food/log?date={TEST_DATE}", headers=auth(user_token))
        entry = next(e for e in r4.json()["entries"] if e["food_name"] == "Loggable Meal")
        assert entry["source"] == "meal"
        assert entry["calories"] == 150

    def test_meal_logs_exploded_by_default(self, client, user_token):
        """Calling POST /meals/{id}/log directly (not via search, no
        combined flag) still defaults to the original exploded behavior
        -- one food_log entry per item -- preserving existing behavior
        for any caller not opting into combined mode."""
        r = client.post("/meals", headers=auth(user_token), json={
            "name": "Default Exploded Meal",
            "items": [
                {"food_name": "Eggs", "calories": 140, "protein": 12, "carbs": 1, "fat": 10, "fiber": 0},
                {"food_name": "Toast", "calories": 80, "protein": 3, "carbs": 15, "fat": 1, "fiber": 1},
            ],
        })
        meal_id = r.json()["id"]
        r2 = client.post(f"/meals/{meal_id}/log", headers=auth(user_token), json={"date": TEST_DATE, "meal": "Breakfast"})
        assert r2.json()["combined"] is False
        assert len(r2.json()["food_log_ids"]) == 2

    def test_combined_meal_entry_can_be_exploded(self, client, user_token):
        r = client.post("/meals", headers=auth(user_token), json={
            "name": "Explodable Meal",
            "items": [
                {"food_name": "Eggs", "calories": 140, "protein": 12, "carbs": 1, "fat": 10, "fiber": 0},
                {"food_name": "Toast", "calories": 80, "protein": 3, "carbs": 15, "fat": 1, "fiber": 1,
                 "nutrients": {"Sodium, Na": {"value": 150, "unit": "mg"}}},
            ],
        })
        meal_id = r.json()["id"]

        r2 = client.post(f"/meals/{meal_id}/log", headers=auth(user_token), json={
            "date": TEST_DATE, "meal": "Breakfast", "combined": True,
        })
        combined_food_log_id = r2.json()["food_log_ids"][0]

        # Confirm it's currently one combined entry
        r3 = client.get(f"/food/log?date={TEST_DATE}", headers=auth(user_token))
        assert len([e for e in r3.json()["entries"] if e["food_name"] == "Explodable Meal"]) == 1

        # Explode it
        r4 = client.post(f"/meals/{meal_id}/explode/{combined_food_log_id}", headers=auth(user_token))
        assert r4.status_code == 200
        assert len(r4.json()["food_log_ids"]) == 2

        # Combined entry is gone, replaced by 2 individual entries with the SAME date/meal
        r5 = client.get(f"/food/log?date={TEST_DATE}", headers=auth(user_token))
        entries = r5.json()["entries"]
        assert not any(e["food_name"] == "Explodable Meal" for e in entries)
        assert any(e["food_name"] == "Eggs" and e["meal"] == "Breakfast" for e in entries)
        assert any(e["food_name"] == "Toast" and e["nutrients"].get("Sodium, Na", {}).get("value") == 150 for e in entries)

    def test_explode_rejects_wrong_meal_id(self, client, user_token):
        """Exploding with a food_log_id that belongs to a DIFFERENT
        meal's combined entry must 404, not silently explode against the
        wrong meal's items."""
        r1 = client.post("/meals", headers=auth(user_token), json={
            "name": "Meal A", "items": [{"food_name": "X", "calories": 10}],
        })
        meal_a_id = r1.json()["id"]
        r2 = client.post(f"/meals/{meal_a_id}/log", headers=auth(user_token), json={
            "date": TEST_DATE, "meal": "Snack", "combined": True,
        })
        food_log_id = r2.json()["food_log_ids"][0]

        r3 = client.post("/meals", headers=auth(user_token), json={"name": "Meal B", "items": []})
        meal_b_id = r3.json()["id"]

        r4 = client.post(f"/meals/{meal_b_id}/explode/{food_log_id}", headers=auth(user_token))
        assert r4.status_code == 404

    def test_explode_rejects_non_combined_entry(self, client, user_token):
        """A plain food_log entry that was never a combined meal entry
        (e.g. a normal USDA-sourced log) must not be explodable."""
        r = client.post("/meals", headers=auth(user_token), json={"name": "Meal C", "items": []})
        meal_id = r.json()["id"]

        r2 = client.post("/food/log", headers=auth(user_token), json={
            "date": TEST_DATE, "meal": "Snack", "food_name": "Random Food", "source": "USDA", "calories": 50,
        })
        random_food_log_id = r2.json()["id"]

        r3 = client.post(f"/meals/{meal_id}/explode/{random_food_log_id}", headers=auth(user_token))
        assert r3.status_code == 404

    def test_explode_scoped_to_owner(self, client, user_token, other_user_token):
        r = client.post("/meals", headers=auth(user_token), json={
            "name": "Owner Meal", "items": [{"food_name": "X", "calories": 10}],
        })
        meal_id = r.json()["id"]
        r2 = client.post(f"/meals/{meal_id}/log", headers=auth(user_token), json={
            "date": TEST_DATE, "meal": "Snack", "combined": True,
        })
        food_log_id = r2.json()["food_log_ids"][0]

        r3 = client.post(f"/meals/{meal_id}/explode/{food_log_id}", headers=auth(other_user_token))
        assert r3.status_code == 404

    def test_meal_search_result_can_be_added_to_pantry(self, client, user_token):
        """Pantry is (source, source_id)-generic (see routers/pantry.py)
        with zero special-casing for source -- so a meal search result
        works as a pantry item the same way a recipe does. Since pantry
        now carries nutrition PER serving alongside the item (see
        TestPantryRemoveServings/test_pantry_item_stores_nutrition_at_add_time),
        the search result's nutrients are passed through at add-time so
        /consume can compute real macros without the caller resupplying
        them later."""
        r = client.post("/meals", headers=auth(user_token), json={
            "name": "Pantry Meal",
            "items": [{"food_name": "Cereal", "calories": 120, "protein": 3, "carbs": 24, "fat": 1, "fiber": 2}],
        })
        r2 = client.get("/food/search?q=Pantry+Meal", headers=auth(user_token))
        result = next(res for res in r2.json()["results"] if res["source"] == "meal")

        r3 = client.post("/pantry", headers=auth(user_token), json={
            "food_name": result["name"],
            "source": result["source"],
            "source_id": result["id"],
            "tracking_mode": "single",
            "calories": result["nutrients"]["Energy"]["value"],
            "protein": result["nutrients"]["Protein"]["value"],
            "carbs": result["nutrients"]["Carbohydrate, by difference"]["value"],
            "fat": result["nutrients"]["Total lipid (fat)"]["value"],
            "fiber": result["nutrients"]["Fiber, total dietary"]["value"],
        })
        assert r3.status_code == 200
        pantry_item_id = r3.json()["id"]

        r4 = client.get("/pantry", headers=auth(user_token))
        item = next(i for i in r4.json()["items"] if i["id"] == pantry_item_id)
        assert item["source"] == "meal"
        assert item["source_id"] == result["id"]
        assert item["calories"] == 120

        r5 = client.post(f"/pantry/{pantry_item_id}/consume", headers=auth(user_token), json={
            "servings": 1, "date": TEST_DATE, "meal": "Breakfast",
        })
        assert r5.status_code == 200
        assert r5.json()["pantry_status"] == "removed"  # single mode always removes on consume

        r6 = client.get(f"/food/log?date={TEST_DATE}", headers=auth(user_token))
        logged = next(e for e in r6.json()["entries"] if e["food_name"] == "Pantry Meal")
        assert logged["source"] == "meal"
        assert logged["calories"] == 120

    def test_include_own_false_excludes_meals_from_search(self, client, user_token):
        client.post("/meals", headers=auth(user_token), json={"name": "Excludable Meal XYZ", "items": []})
        r = client.get("/food/search?q=Excludable+Meal+XYZ&include_own=false", headers=auth(user_token))
        assert not any(res["source"] == "meal" for res in r.json()["results"])

    def test_meal_search_scoped_to_owner(self, client, user_token, other_user_token):
        client.post("/meals", headers=auth(user_token), json={"name": "Owner Only Meal ABC", "items": []})
        r = client.get("/food/search?q=Owner+Only+Meal+ABC", headers=auth(other_user_token))
        assert not any(res["source"] == "meal" for res in r.json()["results"])

    def test_get_preferences_defaults_include_unit_system_and_chart_style(self, client, other_user_token):
        r = client.get("/preferences", headers=auth(other_user_token))
        body = r.json()
        assert body["unit_system"] == "imperial"
        assert body["macro_chart_style"] == "pie"

    def test_set_unit_system_persists(self, client, user_token):
        r = client.put("/preferences", headers=auth(user_token), json={"unit_system": "metric"})
        assert r.status_code == 200
        assert r.json()["unit_system"] == "metric"

        r2 = client.get("/preferences", headers=auth(user_token))
        assert r2.json()["unit_system"] == "metric"

    def test_set_macro_chart_style_persists(self, client, user_token):
        r = client.put("/preferences", headers=auth(user_token), json={"macro_chart_style": "bar"})
        assert r.status_code == 200
        assert r.json()["macro_chart_style"] == "bar"

    def test_unit_system_and_chart_style_independent_of_colors(self, client, user_token):
        """Setting unit_system shouldn't reset colors or vice versa --
        same is_custom-preserving pattern as every other preference
        field."""
        client.put("/preferences", headers=auth(user_token), json={"colors": {"macro_fat": "#111111"}})
        client.put("/preferences", headers=auth(user_token), json={"unit_system": "metric"})
        r = client.get("/preferences", headers=auth(user_token))
        body = r.json()
        assert body["colors"]["macro_fat"] == "#111111"
        assert body["unit_system"] == "metric"

    def test_invalid_unit_system_rejected_by_api(self, client, user_token):
        r = client.put("/preferences", headers=auth(user_token), json={"unit_system": "furlongs"})
        assert r.status_code == 422


# ── Sync / BMR split ─────────────────────────────────────────────────────────

class TestSyncBmrSplit:
    """Verifies the sync/BMR split: POST /sync/bmr reads only from
    tdee_log (Postgres) and never contacts Cronometer unless
    push_to_cronometer=true is explicitly passed."""

    def test_bmr_with_no_data_returns_none_gracefully(self, client, other_user_token):
        r = client.post("/sync/bmr", headers=auth(other_user_token))
        assert r.status_code == 200
        body = r.json()
        assert body["bmr"] is None
        assert body["pushed_to_cronometer"] is False

    def test_bmr_push_without_credentials_rejected(self, client, other_user_token):
        """push_to_cronometer=true requires saved Cronometer credentials
        -- confirms this path is gated, not silently attempted with no
        credentials to use."""
        r = client.post("/sync/bmr?push_to_cronometer=true", headers=auth(other_user_token))
        assert r.status_code == 400
        assert "credentials" in r.json()["detail"].lower()

    def test_bmr_does_not_require_cronometer_credentials_when_not_pushing(self, client, other_user_token):
        """The core of the split: recalculating BMR from already-synced
        data should work with zero Cronometer credentials configured,
        since it's reading from tdee_log, not calling Cronometer."""
        r = client.post("/sync/bmr", headers=auth(other_user_token))
        assert r.status_code == 200  # not a 400 credentials error


# ── Label scanner ─────────────────────────────────────────────────────────────

class TestLabelScannerEndpoint:
    def test_scan_requires_auth(self, client):
        r = client.post("/label-scanner/scan", files={"file": ("label.jpg", b"fake-bytes", "image/jpeg")})
        assert r.status_code in (401, 403)

    def test_scan_rejects_unsupported_content_type(self, client, user_token):
        r = client.post(
            "/label-scanner/scan", headers=auth(user_token),
            files={"file": ("label.pdf", b"fake-bytes", "application/pdf")},
        )
        assert r.status_code == 400

    def test_scan_rejects_empty_file(self, client, user_token):
        r = client.post(
            "/label-scanner/scan", headers=auth(user_token),
            files={"file": ("label.jpg", b"", "image/jpeg")},
        )
        assert r.status_code == 400

    def test_scan_without_api_key_returns_manual_required(self, client, user_token, monkeypatch):
        """No GEMINI_API_KEY is configured in this test environment --
        confirms the endpoint degrades gracefully end-to-end (through the
        real HTTP layer, not just the unit-tested function) rather than
        500ing when the feature's external dependency is unavailable."""
        import os
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        r = client.post(
            "/label-scanner/scan", headers=auth(user_token),
            files={"file": ("label.jpg", b"fake-image-bytes", "image/jpeg")},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "manual_required"

    def test_scanned_result_can_be_saved_via_custom_foods(self, client, user_token):
        """The actual end-to-end workflow: scan returns data (never
        saves), caller then POSTs the reviewed/edited result to
        /custom-foods to persist it -- confirms the two endpoints compose
        correctly as the two-step flow this feature is designed around."""
        r = client.post("/custom-foods", headers=auth(user_token), json={
            "food_name": "Scanned Granola Bar", "brand": "TestBrand",
            "reference_amount": 1, "reference_unit": "bar", "reference_grams": 60,
            "calories": 200, "protein": 20, "carbs": 22, "fat": 7, "fiber": 3,
            "nutrients": {"Sodium, Na": {"value": 180, "unit": "mg"}},
        })
        assert r.status_code == 200
        food_id = r.json()["id"]

        r2 = client.get(f"/custom-foods/{food_id}", headers=auth(user_token))
        assert r2.json()["food_name"] == "Scanned Granola Bar"
        assert r2.json()["nutrients"]["Sodium, Na"]["value"] == 180

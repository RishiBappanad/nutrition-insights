"""
Integration tests for the 2026-09-24 Targets/Goals unification: nutrient and
macro targets are goals-table rows (source 'dri_default' / 'user_target' /
'macro_target'), and the dashboard's per-date progress reads consumption
through the event log's deduplicated view rather than food_log directly.

Same conventions as the other integration suites: real HTTP through
TestClient, JWTs minted directly, far-future dates and per-run account ids.
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
    return 830_000_000 + _RUN_ID * 10 + offset


DAY = "2033-07-04"
OTHER_DAY = "2033-07-05"


@pytest.fixture(scope="module")
def user_token(client):
    token = _mint_token(_account_id(1), f"targets_goals_{_RUN_ID}@example.com")
    r = client.put("/profile", headers=auth(token), json={"age": 29, "sex": "female"})
    assert r.status_code == 200
    return token


def _log_food(client, token, date, calories=100, protein=None, name="Test Food", **extra):
    nutrients = {"Protein": {"value": protein, "unit": "G"}} if protein is not None else {}
    r = client.post("/food/log", headers=auth(token), json={
        "date": date, "meal": "Lunch", "food_name": name, "source": "manual",
        "calories": calories, "nutrients": nutrients, **extra,
    })
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _progress(client, token, date):
    r = client.get(f"/targets/progress?date={date}", headers=auth(token))
    assert r.status_code == 200
    return r.json()["progress"]


class TestDriTargetsAreGoals:
    def test_profile_seeds_targets_with_floor_and_ceiling(self, client, user_token):
        targets = {t["nutrient_name"]: t for t in client.get("/targets/nutrients", headers=auth(user_token)).json()["targets"]}
        assert targets["Iron, Fe"]["daily_target"] == 18
        assert targets["Iron, Fe"]["max_threshold"] == 45
        assert targets["Protein"]["max_threshold"] is None
        assert targets["Iron, Fe"]["is_custom"] is False

    def test_default_goal_list_hides_seeded_nutrient_rows(self, client, user_token):
        assert client.get("/goals", headers=auth(user_token)).json() == []
        everything = client.get("/goals?include_system=true", headers=auth(user_token)).json()
        assert len(everything) > 40
        assert {g["source"] for g in everything} == {"dri_default"}

    def test_reseeding_is_idempotent(self, client, user_token):
        before = len(client.get("/goals?include_system=true", headers=auth(user_token)).json())
        client.put("/profile", headers=auth(user_token), json={"age": 29, "sex": "female"})
        assert len(client.get("/goals?include_system=true", headers=auth(user_token)).json()) == before


class TestOverrideAndRevert:
    def test_override_marks_custom_and_survives_reseed(self, client, user_token):
        r = client.put("/targets/nutrients/Sodium, Na", headers=auth(user_token), json={
            "nutrient_name": "Sodium, Na", "daily_target": 1000, "max_threshold": 1800, "is_custom": True,
        })
        assert r.status_code == 200
        client.put("/profile", headers=auth(user_token), json={"age": 55, "sex": "female"})  # new age bracket -> reseed
        sodium = {t["nutrient_name"]: t for t in client.get("/targets/nutrients", headers=auth(user_token)).json()["targets"]}["Sodium, Na"]
        assert (sodium["daily_target"], sodium["max_threshold"], sodium["is_custom"]) == (1000, 1800, True)

    def test_revert_restores_dri_immediately(self, client, user_token):
        r = client.put("/targets/nutrients/Sodium, Na", headers=auth(user_token), json={
            "nutrient_name": "Sodium, Na", "is_custom": False,
        })
        assert r.status_code == 200
        sodium = {t["nutrient_name"]: t for t in client.get("/targets/nutrients", headers=auth(user_token)).json()["targets"]}["Sodium, Na"]
        assert (sodium["daily_target"], sodium["max_threshold"], sodium["is_custom"]) == (1500, 2300, False)

    def test_removing_a_bound_deletes_that_goal(self, client, user_token):
        client.put("/targets/nutrients/Zinc, Zn", headers=auth(user_token), json={
            "nutrient_name": "Zinc, Zn", "daily_target": 9, "max_threshold": None, "is_custom": True,
        })
        zinc = {t["nutrient_name"]: t for t in client.get("/targets/nutrients", headers=auth(user_token)).json()["targets"]}["Zinc, Zn"]
        assert (zinc["daily_target"], zinc["max_threshold"]) == (9, None)

    def test_override_unknown_nutrient_404(self, client, user_token):
        r = client.put("/targets/nutrients/Unobtainium", headers=auth(user_token), json={
            "nutrient_name": "Unobtainium", "daily_target": 1, "is_custom": True,
        })
        assert r.status_code == 404


class TestMacroTargetsAreGoals:
    def test_macros_round_trip_and_show_as_goals(self, client, user_token):
        r = client.put("/targets/macros", headers=auth(user_token), json={
            "mode": "ratio", "calorie_target": 2000, "protein_pct": 30, "carbs_pct": 40, "fat_pct": 30,
        })
        assert r.status_code == 200
        got = client.get("/targets/macros", headers=auth(user_token)).json()
        assert (got["calorie_target"], got["protein_g"], got["carbs_g"], got["fat_g"]) == (2000, 150.0, 200.0, 66.7)
        goals = {g["label"]: g for g in client.get("/goals", headers=auth(user_token)).json()}
        assert set(goals) == {"Calories", "Protein", "Carbs", "Fat"}
        assert goals["Protein"]["comparator"] == "gte" and goals["Protein"]["unit"] == "g"
        assert goals["Calories"]["unit"] == "kcal" and goals["Carbs"]["unit"] == "g"

    def test_resaving_updates_in_place_not_duplicates(self, client, user_token):
        client.put("/targets/macros", headers=auth(user_token), json={
            "mode": "fixed", "calorie_target": 2500, "protein_g": 180, "carbs_g": 250, "fat_g": 80,
        })
        assert len(client.get("/goals", headers=auth(user_token)).json()) == 4
        assert client.get("/targets/macros", headers=auth(user_token)).json()["calorie_target"] == 2500


class TestProgressReadsTheEventLog:
    def test_logged_food_counts_on_its_own_date_only(self, client, user_token):
        _log_food(client, user_token, DAY, protein=25)
        assert _progress(client, user_token, DAY)["Protein"]["actual"] == 25
        assert _progress(client, user_token, OTHER_DAY)["Protein"]["actual"] == 0

    def test_deleted_entry_stops_counting(self, client, user_token):
        entry_id = _log_food(client, user_token, DAY, protein=10, name="Deleted Later")
        assert _progress(client, user_token, DAY)["Protein"]["actual"] == 35
        assert client.delete(f"/food/log/{entry_id}", headers=auth(user_token)).status_code in (200, 204)
        assert _progress(client, user_token, DAY)["Protein"]["actual"] == 25

    def test_pantry_consume_is_filed_under_the_entrys_date_not_click_time(self, client, user_token):
        """Regression: pantry consume logged its event without the entry's
        date, so occurred_at defaulted to now and the entry counted on the
        wrong day once progress moved onto the event log."""
        r = client.post("/pantry", headers=auth(user_token), json={
            "food_name": "Protein Bars", "tracking_mode": "countable", "remaining_servings": 4,
            "calories": 200, "nutrients": {"Protein": {"value": 20, "unit": "G"}},
        })
        assert r.status_code == 200, r.text
        item_id = r.json().get("id")
        c = client.post(f"/pantry/{item_id}/consume", headers=auth(user_token), json={"servings": 1, "date": OTHER_DAY, "meal": "Snack"})
        assert c.status_code == 200, c.text
        assert _progress(client, user_token, OTHER_DAY)["Protein"]["actual"] == 20
        assert _progress(client, user_token, DAY)["Protein"]["actual"] == 25  # unchanged


class TestNutrientAxisGoals:
    def test_shorthand_nutrient_goal_status(self, client, user_token):
        r = client.post("/goals", headers=auth(user_token), json={
            "measure_field": "nutrient:Protein", "comparator": "gte", "target_amount": 700, "period": "weekly",
        })
        assert r.status_code == 201, r.text
        goal = r.json()
        assert goal["measure_query"]["measureField"] == "nutrient:Protein" and goal["unit"] == "g"
        assert {"field": "owner_type", "operator": "eq", "value": "food_log"} in goal["measure_query"]["filters"]
        assert client.get(f"/goals/{goal['id']}/status", headers=auth(user_token)).status_code == 200

    def test_calorie_goal_ignores_pantry_stock(self, client, user_token):
        """A pantry item carries calories too, but it's inventory, not
        consumption -- the server adds an owner_type=food_log filter so a
        restock can't count toward a calorie goal."""
        r = client.post("/goals", headers=auth(user_token), json={"comparator": "lte", "target_amount": 5000, "period": "daily"})
        goal_id = r.json()["id"]
        client.post("/pantry", headers=auth(user_token), json={
            "food_name": "Big Restock", "tracking_mode": "countable", "remaining_servings": 10, "calories": 900,
        })
        assert client.get(f"/goals/{goal_id}/status", headers=auth(user_token)).json()["measure_value"] < 900

    def test_full_form_query_gets_consumption_filter_too(self, client, user_token):
        r = client.post("/goals", headers=auth(user_token), json={
            "comparator": "lte", "reference_amount": 3000,
            "measure_query": {"aggregation": "sum", "filters": [], "timeWindow": {"kind": "current_period", "period": "weekly"}},
        })
        assert r.status_code == 201
        assert any(f["field"] == "owner_type" for f in r.json()["measure_query"]["filters"])

    def test_invalid_measure_field_rejected(self, client, user_token):
        r = client.post("/goals", headers=auth(user_token), json={
            "measure_field": "protein", "comparator": "gte", "target_amount": 1, "period": "daily",
        })
        assert r.status_code == 400

    def test_measures_and_presets_endpoints(self, client, user_token):
        measures = client.get("/goals/measures").json()["measures"]
        assert measures[0]["label"] == "Calories" and any(m["field"] == "nutrient:Protein" for m in measures)
        presets = client.get("/goals/presets", headers=auth(user_token)).json()
        assert any(p.get("measure_field") for p in presets["basic"])
        assert any(p["measure_query"]["timeWindow"]["period"] == "monthly" for p in presets["advanced"])


class TestUserIsolation:
    def test_another_user_sees_none_of_this(self, client):
        other = _mint_token(_account_id(2), f"targets_goals_other_{_RUN_ID}@example.com")
        assert client.get("/goals?include_system=true", headers=auth(other)).json() == []
        assert client.get("/targets/nutrients", headers=auth(other)).json()["targets"] == []
        assert client.get("/targets/macros", headers=auth(other)).status_code == 404

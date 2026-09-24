"""
Integration tests for the 2026-09-24 second pass on unified Targets/Goals:
editable + resettable presets (the DRI/macro targets, shown as goals),
duplicate-goal rejection ("can't have two protein floors"), goals grouped
by the nutrient they measure with standard units, and vital-based goals
(weight, body fat).

Same conventions as test_targets_as_goals.py: real HTTP through
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
    return 840_000_000 + _RUN_ID * 10 + offset


@pytest.fixture(scope="module")
def user_token(client):
    token = _mint_token(_account_id(1), f"presets_vitals_{_RUN_ID}@example.com")
    assert client.put("/profile", headers=auth(token), json={"age": 29, "sex": "female"}).status_code == 200
    return token


def _goals(client, token):
    return client.get("/goals?include_system=true", headers=auth(token)).json()


def _preset(client, token, nutrient, comparator="gte"):
    for g in _goals(client, token):
        if g["measure_query"].get("measureField") == f"nutrient:{nutrient}" and g["comparator"] == comparator and g["is_preset"]:
            return g
    raise AssertionError(f"no preset for {nutrient} {comparator}")


class TestMeasuresAreGroupedByNutrient:
    def test_groups_and_standard_units(self, client):
        body = client.get("/goals/measures").json()
        by_label = {m["label"]: m for m in body["measures"]}
        assert body["groups"] == ["Macros", "Vitamins", "Minerals", "Other", "Vitals"]
        assert by_label["Calories"]["group"] == "Macros" and by_label["Calories"]["unit"] == "kcal"
        assert by_label["Protein"]["group"] == "Macros" and by_label["Protein"]["unit"] == "g"
        assert by_label["Vitamin C, total ascorbic acid"]["group"] == "Vitamins"
        assert by_label["Vitamin C, total ascorbic acid"]["unit"] == "mg"
        assert by_label["Folate, total"]["unit"] == "µg"
        assert by_label["Sodium, Na"]["group"] == "Minerals" and by_label["Sodium, Na"]["unit"] == "mg"
        assert by_label["Weight"]["group"] == "Vitals" and by_label["Weight"]["unit"] == "lb" and by_label["Weight"]["vital"] == "weight"
        assert by_label["Body fat"]["unit"] == "%"

    def test_measures_come_back_group_by_group(self, client):
        order = ["Macros", "Vitamins", "Minerals", "Other", "Vitals"]
        groups = [m["group"] for m in client.get("/goals/measures").json()["measures"]]
        assert groups == sorted(groups, key=order.index)

    def test_goals_are_described_by_nutrient_group_not_food_category(self, client, user_token):
        vit_c = _preset(client, user_token, "Vitamin C, total ascorbic acid")
        assert vit_c["group"] == "Vitamins" and vit_c["unit"] == "mg" and vit_c["measure_label"] == "Vitamin C, total ascorbic acid"


class TestPresetsAreEditableAndResettable:
    def test_editing_a_preset_amount_customizes_it_and_reset_restores_it(self, client, user_token):
        preset = _preset(client, user_token, "Vitamin C, total ascorbic acid")
        default = preset["reference_amount"]
        assert preset["source"] == "dri_default" and preset["preset_amount"] == default and preset["is_modified"] is False

        r = client.patch(f"/goals/{preset['id']}", headers=auth(user_token), json={"reference_amount": default + 25})
        assert r.status_code == 200, r.text
        edited = r.json()
        assert edited["reference_amount"] == default + 25
        assert edited["source"] == "user_target" and edited["is_modified"] is True and edited["preset_amount"] == default

        r = client.post(f"/goals/{preset['id']}/reset", headers=auth(user_token))
        assert r.status_code == 200, r.text
        assert r.json()["reference_amount"] == default and r.json()["source"] == "dri_default" and r.json()["is_modified"] is False

    def test_customized_preset_survives_a_profile_reseed(self, client, user_token):
        preset = _preset(client, user_token, "Magnesium, Mg")
        client.patch(f"/goals/{preset['id']}", headers=auth(user_token), json={"reference_amount": 999})
        assert client.put("/profile", headers=auth(user_token), json={"age": 29, "sex": "female"}).status_code == 200
        assert _preset(client, user_token, "Magnesium, Mg")["reference_amount"] == 999
        client.post(f"/goals/{preset['id']}/reset", headers=auth(user_token))

    def test_a_preset_cannot_change_what_it_measures(self, client, user_token):
        preset = _preset(client, user_token, "Zinc, Zn")
        for body in ({"comparator": "lte"}, {"severity": "warning"}, {"period": "weekly", "target_amount": 5}):
            assert client.patch(f"/goals/{preset['id']}", headers=auth(user_token), json=body).status_code == 400

    def test_a_preset_cannot_be_deleted(self, client, user_token):
        preset = _preset(client, user_token, "Zinc, Zn")
        assert client.delete(f"/goals/{preset['id']}", headers=auth(user_token)).status_code == 409
        assert any(g["id"] == preset["id"] for g in _goals(client, user_token))

    def test_reset_all_restores_every_customized_preset(self, client, user_token):
        a = _preset(client, user_token, "Calcium, Ca")
        b = _preset(client, user_token, "Iron, Fe")
        for g in (a, b):
            client.patch(f"/goals/{g['id']}", headers=auth(user_token), json={"reference_amount": 1})
        r = client.post("/goals/presets/reset", headers=auth(user_token))
        assert r.status_code == 200 and r.json()["reset"] >= 2
        assert _preset(client, user_token, "Calcium, Ca")["reference_amount"] == a["reference_amount"]
        assert _preset(client, user_token, "Iron, Fe")["source"] == "dri_default"

    def test_reset_needs_a_default_to_reset_to(self, client, user_token):
        own = client.post("/goals", headers=auth(user_token), json={"comparator": "lte", "target_amount": 2100, "period": "daily"}).json()
        assert client.post(f"/goals/{own['id']}/reset", headers=auth(user_token)).status_code == 400
        assert client.patch(f"/goals/{own['id']}", headers=auth(user_token), json={"reference_amount": 2200}).json()["reference_amount"] == 2200

    def test_preset_status_batch_matches_single_status(self, client, user_token):
        preset = _preset(client, user_token, "Sodium, Na", "lte")
        batch = client.get("/goals/statuses", headers=auth(user_token)).json()["statuses"]
        single = client.get(f"/goals/{preset['id']}/status", headers=auth(user_token)).json()
        assert str(preset["id"]) in batch
        for key in ("measure_value", "reference_value", "percent", "on_track"):
            assert batch[str(preset["id"])][key] == single[key]
        # Every active goal, presets included, gets a status.
        assert {str(g["id"]) for g in _goals(client, user_token) if g["is_active"]} <= set(batch)


class TestDuplicatesAreRejected:
    def test_second_protein_floor_is_a_conflict_naming_the_first(self, client, user_token):
        floor = _preset(client, user_token, "Protein")
        before = len(_goals(client, user_token))
        r = client.post("/goals", headers=auth(user_token), json={
            "measure_field": "nutrient:Protein", "comparator": "gte", "target_amount": 150, "period": "daily",
        })
        assert r.status_code == 409
        assert r.json()["detail"]["existing_goal_id"] == floor["id"]
        assert len(_goals(client, user_token)) == before

    def test_a_ceiling_a_warning_tier_and_another_period_are_not_duplicates(self, client, user_token):
        base = {"measure_field": "nutrient:Protein", "target_amount": 200, "period": "daily"}
        assert client.post("/goals", headers=auth(user_token), json={**base, "comparator": "lte"}).status_code == 201
        assert client.post("/goals", headers=auth(user_token), json={**base, "comparator": "gte", "severity": "warning"}).status_code == 201
        assert client.post("/goals", headers=auth(user_token), json={**base, "comparator": "gte", "period": "monthly", "target_amount": 3000}).status_code == 201

    def test_the_same_new_goal_twice_is_rejected_the_second_time(self, client, user_token):
        body = {"measure_field": "nutrient:Caffeine", "comparator": "lte", "target_amount": 400, "period": "daily"}
        assert client.post("/goals", headers=auth(user_token), json=body).status_code == 201
        assert client.post("/goals", headers=auth(user_token), json={**body, "target_amount": 300}).status_code == 409

    def test_editing_into_a_duplicate_is_rejected(self, client, user_token):
        ceiling = client.post("/goals", headers=auth(user_token), json={
            "measure_field": "nutrient:Water", "comparator": "lte", "target_amount": 4000, "period": "daily",
        }).json()
        client.post("/goals", headers=auth(user_token), json={
            "measure_field": "nutrient:Water", "comparator": "gte", "target_amount": 2000, "period": "daily",
        })
        r = client.patch(f"/goals/{ceiling['id']}", headers=auth(user_token), json={
            "measure_field": "nutrient:Water", "comparator": "gte", "target_amount": 2500, "period": "daily",
        })
        assert r.status_code == 409

    def test_setting_macro_targets_leaves_exactly_one_protein_floor(self, client):
        token = _mint_token(_account_id(2), f"presets_macros_{_RUN_ID}@example.com")
        assert client.put("/profile", headers=auth(token), json={"age": 30, "sex": "male"}).status_code == 200
        r = client.put("/targets/macros", headers=auth(token), json={
            "mode": "fixed", "calorie_target": 2400, "protein_g": 170, "carbs_g": 250, "fat_g": 70,
        })
        assert r.status_code == 200, r.text
        floors = [
            g for g in _goals(client, token)
            if g["measure_query"].get("measureField") == "nutrient:Protein" and g["comparator"] == "gte"
            and g["measure_query"]["timeWindow"].get("period") == "daily"
        ]
        assert len(floors) == 1 and floors[0]["reference_amount"] == 170 and floors[0]["source"] == "macro_target"
        # ... and the nutrient targets view (dashboard progress) still sees it.
        protein = next(t for t in client.get("/targets/nutrients", headers=auth(token)).json()["targets"] if t["nutrient_name"] == "Protein")
        assert protein["daily_target"] == 170
        # A profile re-save re-seeds DRI defaults; it must not resurrect a second floor.
        assert client.put("/profile", headers=auth(token), json={"age": 30, "sex": "male"}).status_code == 200
        assert len([g for g in _goals(client, token) if g["measure_query"].get("measureField") == "nutrient:Protein" and g["comparator"] == "gte" and g["measure_query"]["timeWindow"].get("period") == "daily"]) == 1

    def test_presets_endpoint_points_at_the_goal_you_already_have(self, client, user_token):
        floor = _preset(client, user_token, "Protein")
        presets = client.get("/goals/presets", headers=auth(user_token)).json()
        protein = next(p for p in presets["basic"] if p["name"] == "Daily protein floor")
        assert protein["existing_goal_id"] == floor["id"] and protein["group"] == "Macros" and protein["unit"] == "g"
        weight = next(p for p in presets["basic"] if p["name"] == "Goal weight")
        assert weight["group"] == "Vitals" and weight["existing_goal_id"] is None


class TestVitalGoals:
    def test_weight_goal_follows_the_latest_reading(self, client):
        token = _mint_token(_account_id(3), f"vitals_{_RUN_ID}@example.com")
        goal = client.post("/goals", headers=auth(token), json={"vital": "weight", "comparator": "lte", "target_amount": 180}).json()
        assert goal["group"] == "Vitals" and goal["unit"] == "lb" and goal["vital"] == "weight"

        empty = client.get(f"/goals/{goal['id']}/status", headers=auth(token)).json()
        assert empty["has_data"] is False and empty["on_track"] is False and empty["measure_value"] is None

        assert client.post("/vitals", headers=auth(token), json={"metric": "weight", "value": 190, "date": "2033-08-01"}).status_code == 201
        status = client.get(f"/goals/{goal['id']}/status", headers=auth(token)).json()
        assert status["measure_value"] == 190 and status["on_track"] is False and status["has_data"] is True

        client.post("/vitals", headers=auth(token), json={"metric": "weight", "value": 178.5, "date": "2033-08-10"})
        assert client.get(f"/goals/{goal['id']}/status", headers=auth(token)).json()["measure_value"] == 178.5
        # A reading backfilled for an EARLIER day doesn't displace the latest one.
        client.post("/vitals", headers=auth(token), json={"metric": "weight", "value": 200, "date": "2033-07-01"})
        latest = client.get(f"/goals/{goal['id']}/status", headers=auth(token)).json()
        assert latest["measure_value"] == 178.5 and latest["on_track"] is True
        # Correcting a day's reading moves it (an update, not a second reading).
        client.post("/vitals", headers=auth(token), json={"metric": "weight", "value": 181, "date": "2033-08-10"})
        assert client.get(f"/goals/{goal['id']}/status", headers=auth(token)).json()["measure_value"] == 181

    def test_body_fat_goal_and_history(self, client):
        token = _mint_token(_account_id(4), f"bodyfat_{_RUN_ID}@example.com")
        goal = client.post("/goals", headers=auth(token), json={"vital": "body_fat", "comparator": "lte", "target_amount": 18}).json()
        assert goal["unit"] == "%"
        client.post("/vitals", headers=auth(token), json={"metric": "body_fat", "value": 17.2, "date": "2033-08-02"})
        assert client.get(f"/goals/{goal['id']}/status", headers=auth(token)).json()["on_track"] is True
        history = client.get("/vitals/body_fat", headers=auth(token)).json()
        assert history["unit"] == "%" and history["readings"][0] == {"date": "2033-08-02", "value": 17.2}

    def test_manual_weight_endpoint_feeds_the_same_goal(self, client):
        token = _mint_token(_account_id(5), f"weightlog_{_RUN_ID}@example.com")
        goal = client.post("/goals", headers=auth(token), json={"vital": "weight", "comparator": "gte", "target_amount": 150}).json()
        assert client.post("/data/weight", headers=auth(token), json={"date": "2033-08-03", "weight_lbs": 155}).status_code == 200
        assert client.get(f"/goals/{goal['id']}/status", headers=auth(token)).json()["measure_value"] == 155

    def test_a_second_goal_of_the_same_kind_is_a_duplicate(self, client):
        token = _mint_token(_account_id(6), f"vitaldupe_{_RUN_ID}@example.com")
        body = {"vital": "weight", "comparator": "lte", "target_amount": 180}
        assert client.post("/goals", headers=auth(token), json=body).status_code == 201
        assert client.post("/goals", headers=auth(token), json={**body, "target_amount": 175}).status_code == 409
        # ... but a floor on the same vital is a different goal.
        assert client.post("/goals", headers=auth(token), json={**body, "comparator": "gte", "target_amount": 150}).status_code == 201

    def test_vital_inputs_are_validated(self, client):
        token = _mint_token(_account_id(7), f"vitalbad_{_RUN_ID}@example.com")
        assert client.post("/vitals", headers=auth(token), json={"metric": "height", "value": 5}).status_code == 400
        assert client.post("/vitals", headers=auth(token), json={"metric": "body_fat", "value": 100}).status_code == 400
        assert client.post("/vitals", headers=auth(token), json={"metric": "weight", "value": 0}).status_code == 400
        assert client.post("/vitals", headers=auth(token), json={"metric": "weight", "value": 150, "date": "yesterday"}).status_code == 400
        assert client.post("/goals", headers=auth(token), json={"vital": "height", "comparator": "lte", "target_amount": 1}).status_code == 400
        assert client.post("/goals", headers=auth(token), json={"vital": "weight", "measure_field": "nutrient:Protein", "comparator": "lte", "target_amount": 1}).status_code == 400


class TestPreExistingDuplicatesAreFlagged:
    def test_goal_list_flags_a_duplicate_of_a_preset_without_deleting_it(self, client):
        """Data written before the duplicate check existed can hold two
        identical goals. Nothing is deleted on the user's behalf; the extra
        one is flagged. Inserted directly, since the API now refuses to."""
        import asyncio, json
        from app.db import get_pool

        token = _mint_token(_account_id(8), f"legacydupe_{_RUN_ID}@example.com")
        assert client.put("/profile", headers=auth(token), json={"age": 29, "sex": "female"}).status_code == 200
        floor = _preset(client, token, "Protein")

        async def insert_dupe():
            pool = await get_pool()
            return await pool.fetchval(
                """INSERT INTO goals (user_id, severity, comparator, measure_query, reference_amount)
                   SELECT user_id, 'target', 'gte', measure_query, 999 FROM goals WHERE id = $1 RETURNING id""",
                floor["id"],
            )

        dupe_id = client.portal.call(insert_dupe)
        goals = {g["id"]: g for g in _goals(client, token)}
        assert goals[dupe_id]["duplicate_of"] == floor["id"] and goals[floor["id"]]["duplicate_of"] is None
        assert client.delete(f"/goals/{dupe_id}", headers=auth(token)).status_code == 204


class TestGoalsComparedAgainstAnotherMeasure:
    """A goal's reference can be a computation on a DIFFERENT measure, times
    a multiplier -- protein >= 0.0625 x calories is protein >= 25% of
    calories. Evaluated against today (UTC), since a goal's window is
    relative to now."""

    @staticmethod
    def _today():
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).date().isoformat()

    def _log(self, client, token, calories, protein=None):
        nutrients = {"Protein": {"value": protein, "unit": "G"}} if protein is not None else {}
        r = client.post("/food/log", headers=auth(token), json={
            "date": self._today(), "meal": "Lunch", "food_name": "Ratio Test", "source": "manual",
            "calories": calories, "nutrients": nutrients,
        })
        assert r.status_code == 200, r.text

    def _ratio_goal(self, client, token, scale=0.0625, **extra):
        body = {
            "comparator": "gte", "notify_on_crossing": True,
            "measure_query": {"aggregation": "sum", "measureField": "nutrient:Protein", "filters": [], "timeWindow": {"kind": "current_period", "period": "daily"}},
            "reference_query": {"aggregation": "sum", "filters": [], "timeWindow": {"kind": "current_period", "period": "daily"}, "scale": scale},
            **extra,
        }
        return client.post("/goals", headers=auth(token), json=body)

    def test_protein_as_a_share_of_calories(self, client):
        token = _mint_token(_account_id(9), f"ratio_{_RUN_ID}@example.com")
        r = self._ratio_goal(client, token)
        assert r.status_code == 201, r.text
        goal = r.json()
        assert goal["reference_scale"] == 0.0625 and goal["reference_measure"]["label"] == "Calories"
        assert goal["unit"] == "g"  # the goal is still asserted in the measure's own unit

        self._log(client, token, calories=400, protein=30)  # needs 25 g, has 30
        status = client.get(f"/goals/{goal['id']}/status", headers=auth(token)).json()
        assert status["measure_value"] == 30 and status["reference_value"] == 25 and status["on_track"] is True

        # More calories with no protein moves the REFERENCE side: 800 kcal now needs 50 g.
        self._log(client, token, calories=400)
        status = client.get(f"/goals/{goal['id']}/status", headers=auth(token)).json()
        assert status["reference_value"] == 50 and status["on_track"] is False

    def test_a_change_on_only_the_reference_side_logs_the_crossing(self, client):
        token = _mint_token(_account_id(10), f"ratiocross_{_RUN_ID}@example.com")
        goal = self._ratio_goal(client, token).json()
        self._log(client, token, calories=400, protein=30)   # compliant: 30 >= 25
        self._log(client, token, calories=400)               # calories-only event: 30 < 50
        today = self._today()
        events = client.get(f"/events?start={today}&end={today}&event_type=goal_exceeded", headers=auth(token)).json()["events"]
        # This user has exactly one goal, so any goal_exceeded event is its crossing.
        assert len(events) == 1 and goal["label"] is None

    def test_reference_can_be_a_vital(self, client):
        token = _mint_token(_account_id(11), f"ratiovital_{_RUN_ID}@example.com")
        weight = {"aggregation": "last", "filters": [
            {"field": "owner_type", "operator": "eq", "value": "vital"},
            {"field": "category", "operator": "eq", "value": "weight"},
        ], "timeWindow": {"kind": "all_time"}}
        body = {
            "comparator": "gte",
            "measure_query": {"aggregation": "sum", "measureField": "nutrient:Protein", "filters": [], "timeWindow": {"kind": "current_period", "period": "daily"}},
            "reference_query": {**weight, "scale": 0.5},
        }
        goal = client.post("/goals", headers=auth(token), json=body).json()
        empty = client.get(f"/goals/{goal['id']}/status", headers=auth(token)).json()
        assert empty["has_data"] is False and empty["on_track"] is False  # no weight logged yet
        client.post("/vitals", headers=auth(token), json={"metric": "weight", "value": 160, "date": self._today()})
        self._log(client, token, calories=500, protein=90)
        status = client.get(f"/goals/{goal['id']}/status", headers=auth(token)).json()
        assert status["reference_value"] == 80 and status["measure_value"] == 90 and status["on_track"] is True

    def test_scale_is_validated_and_only_valid_on_the_reference(self, client):
        token = _mint_token(_account_id(12), f"ratiobad_{_RUN_ID}@example.com")
        for bad in (0, -1, "2"):
            assert self._ratio_goal(client, token, scale=bad).status_code == 400
        r = client.post("/goals", headers=auth(token), json={
            "comparator": "lte", "reference_amount": 5,
            "measure_query": {"aggregation": "sum", "filters": [], "timeWindow": {"kind": "current_period", "period": "daily"}, "scale": 2},
        })
        assert r.status_code == 400

    def test_same_ratio_with_another_multiplier_is_a_duplicate_and_the_multiplier_is_editable(self, client):
        token = _mint_token(_account_id(13), f"ratiodupe_{_RUN_ID}@example.com")
        goal = self._ratio_goal(client, token).json()
        assert self._ratio_goal(client, token, scale=0.05).status_code == 409
        # A different reference measure is a different goal, not a duplicate.
        other = client.post("/goals", headers=auth(token), json={
            "comparator": "gte",
            "measure_query": {"aggregation": "sum", "measureField": "nutrient:Protein", "filters": [], "timeWindow": {"kind": "current_period", "period": "daily"}},
            "reference_query": {"aggregation": "sum", "measureField": "nutrient:Carbohydrate, by difference", "filters": [], "timeWindow": {"kind": "current_period", "period": "daily"}, "scale": 0.5},
        })
        assert other.status_code == 201, other.text
        edited = client.patch(f"/goals/{goal['id']}", headers=auth(token), json={"reference_scale": 0.1})
        assert edited.status_code == 200 and edited.json()["reference_scale"] == 0.1
        assert client.patch(f"/goals/{goal['id']}", headers=auth(token), json={"reference_scale": 0}).status_code == 400

    def test_ratio_presets_are_offered(self, client, user_token):
        advanced = client.get("/goals/presets", headers=auth(user_token)).json()["advanced"]
        names = [p["name"] for p in advanced]
        assert "Protein ≥ 25% of calories" in names and "Sodium ≤ 1 mg per calorie" in names
        preset = next(p for p in advanced if p["name"] == "Protein ≥ 25% of calories")
        assert preset["reference_query"]["scale"] == 0.0625

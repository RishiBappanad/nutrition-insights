"""
Unit tests for pure functions: dri_reference, nutrition_targets (macro
derivation), food.py's nutrient normalization, water.py's default target
resolution. No database or network access — these test logic in isolation.
"""
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


# ── dri_reference ────────────────────────────────────────────────────────────

class TestDRIReference:
    def test_get_targets_for_returns_all_tracked_nutrients(self):
        from dri_reference import get_targets_for, DRI_TABLE
        result = get_targets_for("male", 30)
        assert set(result.keys()) == set(DRI_TABLE.keys())

    def test_iron_sex_difference_premenopausal(self):
        """The nutrient most likely to be transcribed backwards — confirm
        premenopausal female RDA (18mg) differs correctly from male (8mg)."""
        from dri_reference import get_targets_for
        assert get_targets_for("female", 28)["Iron, Fe"]["daily_target"] == 18
        assert get_targets_for("male", 28)["Iron, Fe"]["daily_target"] == 8

    def test_iron_postmenopausal_matches_male(self):
        from dri_reference import get_targets_for
        assert get_targets_for("female", 55)["Iron, Fe"]["daily_target"] == 8

    def test_vitamin_d_increases_with_age(self):
        from dri_reference import get_targets_for
        young = get_targets_for("male", 30)["Vitamin D (D2 + D3)"]["daily_target"]
        old = get_targets_for("male", 75)["Vitamin D (D2 + D3)"]["daily_target"]
        assert old > young

    def test_calcium_increases_for_older_females(self):
        from dri_reference import get_targets_for
        younger = get_targets_for("female", 40)["Calcium, Ca"]["daily_target"]
        older = get_targets_for("female", 60)["Calcium, Ca"]["daily_target"]
        assert older > younger

    def test_nutrients_without_established_ul_return_none(self):
        from dri_reference import get_targets_for
        result = get_targets_for("male", 30)
        assert result["Potassium, K"]["max_threshold"] is None
        assert result["Vitamin B-12"]["max_threshold"] is None

    def test_invalid_sex_raises(self):
        from dri_reference import get_targets_for
        with pytest.raises(ValueError):
            get_targets_for("other", 30)

    def test_age_below_19_falls_back_to_youngest_adult_bracket(self):
        from dri_reference import get_targets_for
        assert get_targets_for("male", 10) == get_targets_for("male", 25)

    def test_age_over_130_still_resolves_to_70_plus_bracket(self):
        from dri_reference import get_targets_for
        assert get_targets_for("female", 200) == get_targets_for("female", 85)

    @pytest.mark.parametrize("age", [19, 30, 31, 50, 51, 70, 71, 90])
    def test_all_adult_ages_resolve_without_error(self, age):
        from dri_reference import get_targets_for
        for sex in ("male", "female"):
            result = get_targets_for(sex, age)
            assert len(result) > 0


# ── nutrition_targets: derive_macro_grams ───────────────────────────────────

class TestDeriveMacroGrams:
    def test_fixed_mode_passes_through(self):
        from app.nutrition_targets import derive_macro_grams
        result = derive_macro_grams(mode="fixed", calorie_target=2000, protein_g=150, carbs_g=200, fat_g=60)
        assert result == {"calorie_target": 2000, "protein_g": 150, "carbs_g": 200, "fat_g": 60}

    def test_fixed_mode_missing_field_raises(self):
        from app.nutrition_targets import derive_macro_grams
        with pytest.raises(ValueError):
            derive_macro_grams(mode="fixed", calorie_target=2000, protein_g=150)

    def test_ratio_mode_derives_grams_correctly(self):
        """40/30/30 split of 2000 kcal: protein 200g@4kcal=800kcal -> 200g,
        carbs same math, fat 30%=600kcal/9kcal/g=66.7g."""
        from app.nutrition_targets import derive_macro_grams
        result = derive_macro_grams(
            mode="ratio", calorie_target=2000,
            protein_pct=40, carbs_pct=30, fat_pct=30,
        )
        assert result["protein_g"] == pytest.approx(200.0, abs=0.1)
        assert result["carbs_g"] == pytest.approx(150.0, abs=0.1)
        assert result["fat_g"] == pytest.approx(66.7, abs=0.1)

    def test_ratio_mode_percentages_not_summing_to_100_raises(self):
        from app.nutrition_targets import derive_macro_grams
        with pytest.raises(ValueError, match="sum to 100"):
            derive_macro_grams(mode="ratio", calorie_target=2000, protein_pct=40, carbs_pct=30, fat_pct=40)

    def test_ratio_mode_allows_small_float_rounding_tolerance(self):
        from app.nutrition_targets import derive_macro_grams
        # 33.3 + 33.3 + 33.4 = 100.0 exactly, but real-world floats might be 99.9999
        result = derive_macro_grams(mode="ratio", calorie_target=1800, protein_pct=33.3, carbs_pct=33.3, fat_pct=33.4)
        assert result["calorie_target"] == 1800

    def test_ratio_mode_calories_change_updates_grams_proportionally(self):
        """The core reason ratio mode exists: grams should scale with
        calorie_target, not be a stale snapshot."""
        from app.nutrition_targets import derive_macro_grams
        low = derive_macro_grams(mode="ratio", calorie_target=1500, protein_pct=30, carbs_pct=40, fat_pct=30)
        high = derive_macro_grams(mode="ratio", calorie_target=3000, protein_pct=30, carbs_pct=40, fat_pct=30)
        assert high["protein_g"] == pytest.approx(low["protein_g"] * 2, abs=0.1)

    def test_unknown_mode_raises(self):
        from app.nutrition_targets import derive_macro_grams
        with pytest.raises(ValueError, match="unknown macro target mode"):
            derive_macro_grams(mode="auto_from_burn", calorie_target=2000)


# ── food.py: nutrients_to_rows ───────────────────────────────────────────────

class TestNutrientsToRows:
    def test_normalizes_valid_nutrients(self):
        from app.routers.food import nutrients_to_rows
        nutrients = {"Sodium, Na": {"value": 5.0, "unit": "mg"}, "Iron, Fe": {"value": "1.2", "unit": "mg"}}
        rows = nutrients_to_rows(42, nutrients)
        assert (42, "Sodium, Na", 5.0, "mg") in rows
        assert (42, "Iron, Fe", 1.2, "mg") in rows

    def test_skips_entries_with_missing_value(self):
        from app.routers.food import nutrients_to_rows
        nutrients = {"Sodium, Na": {"unit": "mg"}}  # no "value" key
        rows = nutrients_to_rows(1, nutrients)
        assert rows == []

    def test_skips_non_numeric_value(self):
        from app.routers.food import nutrients_to_rows
        nutrients = {"Sodium, Na": {"value": "not-a-number", "unit": "mg"}}
        rows = nutrients_to_rows(1, nutrients)
        assert rows == []

    def test_skips_malformed_entries_gracefully(self):
        """A food source with a gap in nutrient coverage (a bare string
        instead of a {value, unit} dict) shouldn't crash the whole log
        request — one bad nutrient entry is skipped, not fatal."""
        from app.routers.food import nutrients_to_rows
        nutrients = {"Weird Field": "unexpected string", "Sodium, Na": {"value": 5, "unit": "mg"}}
        rows = nutrients_to_rows(1, nutrients)
        assert rows == [(1, "Sodium, Na", 5.0, "mg")]

    def test_empty_nutrients_returns_empty_list(self):
        from app.routers.food import nutrients_to_rows
        assert nutrients_to_rows(1, {}) == []

    def test_missing_unit_defaults_to_empty_string(self):
        from app.routers.food import nutrients_to_rows
        rows = nutrients_to_rows(1, {"Vitamin C": {"value": 10}})
        assert rows == [(1, "Vitamin C", 10.0, "")]


# ── water.py: default_water_target_ml ───────────────────────────────────────

class TestDefaultWaterTarget:
    def test_female_default(self):
        from app.routers.water import default_water_target_ml
        assert default_water_target_ml("female") == 1420.0

    def test_male_default(self):
        from app.routers.water import default_water_target_ml
        assert default_water_target_ml("male") == 1890.0

    def test_case_insensitive(self):
        from app.routers.water import default_water_target_ml
        assert default_water_target_ml("FEMALE") == 1420.0
        assert default_water_target_ml("Male") == 1890.0

    def test_unknown_value_falls_back_to_male_default(self):
        from app.routers.water import default_water_target_ml
        assert default_water_target_ml("unspecified") == 1890.0


# ── portion_scaling ──────────────────────────────────────────────────────────

class TestPortionScaling:
    def test_gram_based_factor_doubles_when_weight_doubles(self):
        from app.portion_scaling import gram_based_factor
        assert gram_based_factor(from_grams=100, to_grams=200) == 2.0

    def test_gram_based_factor_halves(self):
        from app.portion_scaling import gram_based_factor
        assert gram_based_factor(from_grams=100, to_grams=50) == 0.5

    def test_gram_based_factor_zero_reference_raises(self):
        from app.portion_scaling import gram_based_factor
        with pytest.raises(ValueError):
            gram_based_factor(from_grams=0, to_grams=100)

    def test_gram_based_factor_negative_target_raises(self):
        from app.portion_scaling import gram_based_factor
        with pytest.raises(ValueError):
            gram_based_factor(from_grams=100, to_grams=-10)

    def test_multiple_based_factor_is_identity(self):
        from app.portion_scaling import multiple_based_factor
        assert multiple_based_factor(3) == 3
        assert multiple_based_factor(0.5) == 0.5

    def test_scale_macros_100g_to_200g_doubles_calories(self):
        """The exact scenario from the user's ask: 100g -> 100 kcal,
        200g should give 200 kcal and all macros doubled."""
        from app.portion_scaling import scale_macros
        macros = {"calories": 100, "protein": 5, "carbs": 20, "fat": 2, "fiber": 3}
        scaled = scale_macros(macros, factor=2.0)
        assert scaled == {"calories": 200.0, "protein": 10.0, "carbs": 40.0, "fat": 4.0, "fiber": 6.0}

    def test_scale_nutrients_scales_every_entry_uniformly(self):
        from app.portion_scaling import scale_nutrients
        nutrients = {"Sodium, Na": {"value": 10, "unit": "mg"}, "Iron, Fe": {"value": 1, "unit": "mg"}}
        scaled = scale_nutrients(nutrients, factor=2.5)
        assert scaled["Sodium, Na"] == {"value": 25.0, "unit": "mg"}
        assert scaled["Iron, Fe"] == {"value": 2.5, "unit": "mg"}

    def test_scale_nutrients_preserves_units_unchanged(self):
        from app.portion_scaling import scale_nutrients
        nutrients = {"Vitamin C, total ascorbic acid": {"value": 8.7, "unit": "mg"}}
        scaled = scale_nutrients(nutrients, factor=3.0)
        assert scaled["Vitamin C, total ascorbic acid"]["unit"] == "mg"

    def test_scale_nutrients_skips_malformed_entries(self):
        from app.portion_scaling import scale_nutrients
        nutrients = {"Bad": "not-a-dict", "Good": {"value": 4, "unit": "g"}}
        scaled = scale_nutrients(nutrients, factor=1.0)
        assert "Bad" not in scaled
        assert scaled["Good"] == {"value": 4.0, "unit": "g"}

    def test_negative_factor_rejected(self):
        from app.portion_scaling import scale_macros, scale_nutrients
        with pytest.raises(ValueError):
            scale_macros({"calories": 100}, factor=-1)
        with pytest.raises(ValueError):
            scale_nutrients({"X": {"value": 1, "unit": "g"}}, factor=-1)

    def test_scale_food_entry_grams_mode_end_to_end(self):
        """100g reference giving 100 calories -> requesting 200g should
        give exactly 200 calories, matching the user's stated example."""
        from app.portion_scaling import scale_food_entry
        result = scale_food_entry(
            macros={"calories": 100, "protein": 2, "carbs": 20, "fat": 1, "fiber": 2},
            nutrients={"Sodium, Na": {"value": 5, "unit": "mg"}},
            mode="grams", from_grams=100, to_grams=200,
        )
        assert result["factor"] == 2.0
        assert result["macros"]["calories"] == 200.0
        assert result["nutrients"]["Sodium, Na"]["value"] == 10.0

    def test_scale_food_entry_multiple_mode_end_to_end(self):
        from app.portion_scaling import scale_food_entry
        result = scale_food_entry(
            macros={"calories": 50, "protein": 1, "carbs": 10, "fat": 0.5, "fiber": 1},
            nutrients={},
            mode="multiple", servings_requested=3,
        )
        assert result["factor"] == 3
        assert result["macros"]["calories"] == 150.0

    def test_scale_food_entry_missing_required_fields_raises(self):
        from app.portion_scaling import scale_food_entry
        with pytest.raises(ValueError):
            scale_food_entry(macros={}, nutrients={}, mode="grams", from_grams=100)  # missing to_grams
        with pytest.raises(ValueError):
            scale_food_entry(macros={}, nutrients={}, mode="multiple")  # missing servings_requested

    def test_scale_food_entry_unknown_mode_raises(self):
        from app.portion_scaling import scale_food_entry
        with pytest.raises(ValueError, match="unknown scaling mode"):
            scale_food_entry(macros={}, nutrients={}, mode="volume")

    def test_factor_of_one_is_a_no_op(self):
        from app.portion_scaling import scale_macros
        macros = {"calories": 87, "protein": 3, "carbs": 15, "fat": 1, "fiber": 2}
        assert scale_macros(macros, factor=1.0) == {k: float(v) for k, v in macros.items()}


# ── label_scanner ────────────────────────────────────────────────────────────

class TestLabelScannerConfidence:
    def test_confidence_full_data_scores_high(self):
        from app.routers.label_scanner import _confidence_score
        parsed = {
            "food_name": "Granola Bar", "calories": 150,
            "reference_amount": 1, "nutrients": {"Sodium, Na": {"value": 90, "unit": "mg"}},
        }
        assert _confidence_score(parsed) == 1.0

    def test_confidence_no_data_scores_zero(self):
        from app.routers.label_scanner import _confidence_score
        assert _confidence_score({}) == 0.0

    def test_confidence_partial_data_scores_partial(self):
        from app.routers.label_scanner import _confidence_score
        parsed = {"food_name": "Some Food"}
        assert _confidence_score(parsed) == 0.25

    def test_confidence_reference_grams_counts_same_as_reference_amount(self):
        from app.routers.label_scanner import _confidence_score
        assert _confidence_score({"reference_grams": 100}) == _confidence_score({"reference_amount": 1})


class TestLabelScannerNoApiKey:
    def test_missing_api_key_returns_manual_required(self, monkeypatch):
        """Without GEMINI_API_KEY configured, extraction should return a
        graceful manual_required status immediately -- never raise, never
        make a network call. Confirmed no Gemini wiring existed anywhere
        in this backend before this feature (see label_scanner.py
        module docstring) -- this is the expected default state today."""
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        from app.routers.label_scanner import extract_label_with_gemini
        result = extract_label_with_gemini(b"fake-image-bytes", "image/jpeg")
        assert result.status == "manual_required"
        assert result.confidence == 0.0
        assert "GEMINI_API_KEY" in result.error


class TestLabelScannerGeminiResponseParsing:
    """Mocks the Gemini HTTP call to test response parsing without
    depending on network access or a real API key — mirrors how
    finance-tracker's receipt-ocr tests would need to isolate the parsing
    logic from the live external call."""

    def _mock_gemini_response(self, monkeypatch, response_text: str, status_code: int = 200):
        import app.routers.label_scanner as label_scanner_module

        class FakeResponse:
            def __init__(self):
                self.ok = status_code == 200
                self.status_code = status_code

            def json(self):
                return {"candidates": [{"content": {"parts": [{"text": response_text}]}}]}

        def fake_post(*args, **kwargs):
            return FakeResponse()

        monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-testing")
        monkeypatch.setattr(label_scanner_module.requests, "post", fake_post)

    def test_successful_extraction_parses_all_fields(self, monkeypatch):
        response_json = """```json
        {
            "food_name": "Protein Bar", "brand": "TestBrand",
            "reference_amount": 1, "reference_unit": "bar", "reference_grams": 60,
            "calories": 200, "protein": 20, "carbs": 22, "fat": 7, "fiber": 3,
            "nutrients": {"Sodium, Na": {"value": 180, "unit": "mg"}}
        }
        ```"""
        self._mock_gemini_response(monkeypatch, response_json)
        from app.routers.label_scanner import extract_label_with_gemini
        result = extract_label_with_gemini(b"fake-bytes", "image/jpeg")
        assert result.status == "success"
        assert result.food_name == "Protein Bar"
        assert result.calories == 200
        assert result.nutrients["Sodium, Na"]["value"] == 180

    def test_low_confidence_extraction_returns_partial(self, monkeypatch):
        self._mock_gemini_response(monkeypatch, '{"food_name": "Unclear Item"}')
        from app.routers.label_scanner import extract_label_with_gemini
        result = extract_label_with_gemini(b"fake-bytes", "image/jpeg")
        assert result.status == "partial"
        assert result.confidence < 0.5

    def test_unparseable_response_returns_failed(self, monkeypatch):
        self._mock_gemini_response(monkeypatch, "I cannot read this label, sorry.")
        from app.routers.label_scanner import extract_label_with_gemini
        result = extract_label_with_gemini(b"fake-bytes", "image/jpeg")
        assert result.status == "failed"
        assert result.error is not None

    def test_malformed_json_returns_failed_not_raises(self, monkeypatch):
        self._mock_gemini_response(monkeypatch, '{"food_name": "Bad JSON", "calories": }')
        from app.routers.label_scanner import extract_label_with_gemini
        result = extract_label_with_gemini(b"fake-bytes", "image/jpeg")
        assert result.status == "failed"

    def test_nutrients_with_non_numeric_value_are_skipped(self, monkeypatch):
        response_json = '{"food_name": "X", "calories": 100, "nutrients": {"Bad": {"value": "n/a", "unit": "mg"}, "Good": {"value": 5, "unit": "mg"}}}'
        self._mock_gemini_response(monkeypatch, response_json)
        from app.routers.label_scanner import extract_label_with_gemini
        result = extract_label_with_gemini(b"fake-bytes", "image/jpeg")
        assert "Bad" not in result.nutrients
        assert result.nutrients["Good"]["value"] == 5.0

    def test_api_error_status_returns_failed(self, monkeypatch):
        import app.routers.label_scanner as label_scanner_module

        class FakeErrorResponse:
            ok = False
            status_code = 500

        monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
        monkeypatch.setattr(label_scanner_module.requests, "post", lambda *a, **k: FakeErrorResponse())

        from app.routers.label_scanner import extract_label_with_gemini
        result = extract_label_with_gemini(b"fake-bytes", "image/jpeg")
        assert result.status == "failed"
        assert "500" in result.error

    def test_network_failure_returns_failed_gracefully(self, monkeypatch):
        import app.routers.label_scanner as label_scanner_module
        import requests as requests_module

        def raise_connection_error(*args, **kwargs):
            raise requests_module.exceptions.ConnectionError("network down")

        monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
        monkeypatch.setattr(label_scanner_module.requests, "post", raise_connection_error)

        from app.routers.label_scanner import extract_label_with_gemini
        result = extract_label_with_gemini(b"fake-bytes", "image/jpeg")
        assert result.status == "failed"
        assert "unavailable" in result.error.lower()


# ── preferences ──────────────────────────────────────────────────────────────

class TestPreferencesValidation:
    def test_valid_hex_color_accepted(self):
        from app.routers.preferences import PreferencesRequest
        req = PreferencesRequest(colors={"macro_protein": "#ff00aa"})
        assert req.colors["macro_protein"] == "#ff00aa"

    def test_unknown_color_key_rejected(self):
        from app.routers.preferences import PreferencesRequest
        import pydantic
        with pytest.raises(pydantic.ValidationError, match="unknown color key"):
            PreferencesRequest(colors={"totally_made_up_key": "#ff00aa"})

    def test_malformed_hex_rejected(self):
        from app.routers.preferences import PreferencesRequest
        import pydantic
        with pytest.raises(pydantic.ValidationError):
            PreferencesRequest(colors={"macro_protein": "not-a-color"})

    def test_hex_without_hash_rejected(self):
        from app.routers.preferences import PreferencesRequest
        import pydantic
        with pytest.raises(pydantic.ValidationError):
            PreferencesRequest(colors={"macro_protein": "ff00aa"})

    def test_short_hex_rejected(self):
        """3-digit hex shorthand (#fff) is valid CSS but not accepted here
        -- the validator requires the full 6-digit form for consistency,
        avoiding two valid representations of the same color needing to
        be normalized elsewhere."""
        from app.routers.preferences import PreferencesRequest
        import pydantic
        with pytest.raises(pydantic.ValidationError):
            PreferencesRequest(colors={"macro_protein": "#fff"})

    def test_threshold_out_of_range_rejected(self):
        from app.routers.preferences import PreferencesRequest
        import pydantic
        with pytest.raises(pydantic.ValidationError):
            PreferencesRequest(sufficiency_threshold_pct=150)
        with pytest.raises(pydantic.ValidationError):
            PreferencesRequest(sufficiency_threshold_pct=0)

    def test_threshold_in_range_accepted(self):
        from app.routers.preferences import PreferencesRequest
        req = PreferencesRequest(sufficiency_threshold_pct=85)
        assert req.sufficiency_threshold_pct == 85

    def test_empty_colors_and_none_threshold_valid(self):
        """An empty preferences update (no colors, no threshold change) is
        a valid no-op request, not an error."""
        from app.routers.preferences import PreferencesRequest
        req = PreferencesRequest()
        assert req.colors == {}
        assert req.sufficiency_threshold_pct is None

    def test_default_colors_cover_every_hardcoded_value_replaced(self):
        """Every color key this feature replaces across dashboard,
        charts, and lift-insights must have a documented default --
        otherwise a user who's never set preferences would see a missing
        color (undefined -> invalid CSS) instead of the original
        hardcoded appearance."""
        from app.routers.preferences import DEFAULT_COLORS
        expected_keys = {
            "macro_protein", "macro_carbs", "macro_fat", "macro_alcohol",
            "nutrient_insufficient", "nutrient_sufficient", "nutrient_excess",
            "chart_line_1", "chart_line_2", "chart_line_3", "lift_scatter",
        }
        assert set(DEFAULT_COLORS.keys()) == expected_keys
        for key, value in DEFAULT_COLORS.items():
            import re
            assert re.match(r"^#[0-9a-fA-F]{6}$", value), f"{key} has an invalid default: {value}"

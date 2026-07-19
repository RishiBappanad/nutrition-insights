"""
Dietary Reference Intakes (DRI) — RDA/AI (Daily Target) and UL (Maximum
Threshold) reference values, by sex and age group, for adults.

Source: Food and Nutrition Board, Institute of Medicine, National
Academy of Sciences — "DRI Dietary Reference Intakes: Applications in
Dietary Assessment" (2000), Summary Tables. NIH Bookshelf NBK222881:
https://www.ncbi.nlm.nih.gov/books/NBK222881/
Tolerable Upper Intake Levels: NIH Bookshelf NBK278991 (Table 18):
https://www.ncbi.nlm.nih.gov/books/NBK278991/table/diet-treatment-obes.table18die/

Scope: adult life stages only (19-30, 31-50, 51-70, 70+), male/female —
this app doesn't serve pediatric users, so infant/child DRI rows are
deliberately not included. Pregnancy/lactation values also excluded for
now (a real, separate life-stage category Cronometer supports; flagged
as a future addition if needed, not silently approximated here).

Values marked with an asterisk in the source (Adequate Intake, used when
no RDA has been established) are included identically to RDA values in
this table — DRI convention is that both AI and RDA serve as the target
number; the distinction is about the confidence level behind the number,
not a different target. Nutrients with "ND" (Not Determinable) for UL are
represented as None (no maximum threshold, matches Cronometer's own
"No Target" convention for nutrients without an established DRI value).

Units match what USDA FoodData Central nutrient names use (see
integrations/food_search.py), so target values can be compared directly
against food_log_nutrients rows without a unit-conversion layer:
  - energy/macros: kcal, g
  - most vitamins/minerals: mg or ug (micrograms), per-nutrient below
"""
from typing import Optional, TypedDict


class NutrientDRI(TypedDict):
    unit: str
    rda_ai: dict  # {"male": {...age_bracket: value...}, "female": {...}}
    ul: dict  # same shape, value may be None


AGE_BRACKETS = ["19-30", "31-50", "51-70", "70+"]


def _same_across_brackets(value: Optional[float]) -> dict:
    """Helper for nutrients whose adult RDA/UL doesn't vary by age bracket."""
    return {b: value for b in AGE_BRACKETS}


# Each nutrient: rda_ai/ul dicts keyed "male"/"female", each holding one
# value per AGE_BRACKETS entry. None = no established value (source's "ND").
DRI_TABLE: dict[str, NutrientDRI] = {
    # ── Macronutrients (RDA, distinct from the macro_target_settings table's
    # user-configurable calorie/ratio targets — these are the DRI-default
    # fallback if a user never sets custom macro targets) ──────────────────
    "Protein": {
        "unit": "g",
        "rda_ai": {"male": _same_across_brackets(56), "female": _same_across_brackets(46)},
        "ul": {"male": _same_across_brackets(None), "female": _same_across_brackets(None)},
    },
    "Fiber, total dietary": {
        "unit": "g",
        "rda_ai": {
            "male": {"19-30": 38, "31-50": 38, "51-70": 30, "70+": 30},
            "female": {"19-30": 25, "31-50": 25, "51-70": 21, "70+": 21},
        },
        "ul": {"male": _same_across_brackets(None), "female": _same_across_brackets(None)},
    },
    # ── Vitamins ─────────────────────────────────────────────────────────
    "Vitamin C, total ascorbic acid": {
        "unit": "mg",
        "rda_ai": {"male": _same_across_brackets(90), "female": _same_across_brackets(75)},
        "ul": {"male": _same_across_brackets(2000), "female": _same_across_brackets(2000)},
    },
    "Thiamin": {
        "unit": "mg",
        "rda_ai": {"male": _same_across_brackets(1.2), "female": _same_across_brackets(1.1)},
        "ul": {"male": _same_across_brackets(None), "female": _same_across_brackets(None)},
    },
    "Riboflavin": {
        "unit": "mg",
        "rda_ai": {"male": _same_across_brackets(1.3), "female": _same_across_brackets(1.1)},
        "ul": {"male": _same_across_brackets(None), "female": _same_across_brackets(None)},
    },
    "Niacin": {
        "unit": "mg",
        "rda_ai": {"male": _same_across_brackets(16), "female": _same_across_brackets(14)},
        "ul": {"male": _same_across_brackets(35), "female": _same_across_brackets(35)},
    },
    "Vitamin B-6": {
        "unit": "mg",
        "rda_ai": {
            "male": {"19-30": 1.3, "31-50": 1.3, "51-70": 1.7, "70+": 1.7},
            "female": {"19-30": 1.3, "31-50": 1.3, "51-70": 1.5, "70+": 1.5},
        },
        "ul": {"male": _same_across_brackets(100), "female": _same_across_brackets(100)},
    },
    "Folate, total": {
        "unit": "ug",
        "rda_ai": {"male": _same_across_brackets(400), "female": _same_across_brackets(400)},
        "ul": {"male": _same_across_brackets(1000), "female": _same_across_brackets(1000)},
    },
    "Vitamin B-12": {
        "unit": "ug",
        "rda_ai": {"male": _same_across_brackets(2.4), "female": _same_across_brackets(2.4)},
        "ul": {"male": _same_across_brackets(None), "female": _same_across_brackets(None)},
    },
    "Pantothenic acid": {
        "unit": "mg",
        "rda_ai": {"male": _same_across_brackets(5), "female": _same_across_brackets(5)},
        "ul": {"male": _same_across_brackets(None), "female": _same_across_brackets(None)},
    },
    "Biotin": {
        "unit": "ug",
        "rda_ai": {"male": _same_across_brackets(30), "female": _same_across_brackets(30)},
        "ul": {"male": _same_across_brackets(None), "female": _same_across_brackets(None)},
    },
    "Choline, total": {
        "unit": "mg",
        "rda_ai": {"male": _same_across_brackets(550), "female": _same_across_brackets(425)},
        "ul": {"male": _same_across_brackets(3500), "female": _same_across_brackets(3500)},
    },
    "Vitamin A, RAE": {
        "unit": "ug",
        "rda_ai": {"male": _same_across_brackets(900), "female": _same_across_brackets(700)},
        "ul": {"male": _same_across_brackets(3000), "female": _same_across_brackets(3000)},
    },
    "Vitamin E (alpha-tocopherol)": {
        "unit": "mg",
        "rda_ai": {"male": _same_across_brackets(15), "female": _same_across_brackets(15)},
        "ul": {"male": _same_across_brackets(1000), "female": _same_across_brackets(1000)},
    },
    "Vitamin D (D2 + D3)": {
        "unit": "ug",
        "rda_ai": {
            "male": {"19-30": 5, "31-50": 5, "51-70": 10, "70+": 15},
            "female": {"19-30": 5, "31-50": 5, "51-70": 10, "70+": 15},
        },
        "ul": {"male": _same_across_brackets(50), "female": _same_across_brackets(50)},
    },
    # ── Minerals / elements ──────────────────────────────────────────────
    "Calcium, Ca": {
        "unit": "mg",
        "rda_ai": {
            "male": {"19-30": 1000, "31-50": 1000, "51-70": 1000, "70+": 1200},
            "female": {"19-30": 1000, "31-50": 1000, "51-70": 1200, "70+": 1200},
        },
        "ul": {"male": _same_across_brackets(2500), "female": _same_across_brackets(2500)},
    },
    "Phosphorus, P": {
        "unit": "mg",
        "rda_ai": {"male": _same_across_brackets(700), "female": _same_across_brackets(700)},
        "ul": {
            "male": {"19-30": 4000, "31-50": 4000, "51-70": 4000, "70+": 3000},
            "female": {"19-30": 4000, "31-50": 4000, "51-70": 4000, "70+": 3000},
        },
    },
    "Magnesium, Mg": {
        "unit": "mg",
        "rda_ai": {
            "male": {"19-30": 400, "31-50": 420, "51-70": 420, "70+": 420},
            "female": {"19-30": 310, "31-50": 320, "51-70": 320, "70+": 320},
        },
        "ul": {"male": _same_across_brackets(350), "female": _same_across_brackets(350)},
    },
    "Iron, Fe": {
        "unit": "mg",
        "rda_ai": {"male": _same_across_brackets(8), "female": {"19-30": 18, "31-50": 18, "51-70": 8, "70+": 8}},
        "ul": {"male": _same_across_brackets(45), "female": _same_across_brackets(45)},
    },
    "Zinc, Zn": {
        "unit": "mg",
        "rda_ai": {"male": _same_across_brackets(11), "female": _same_across_brackets(8)},
        "ul": {"male": _same_across_brackets(40), "female": _same_across_brackets(40)},
    },
    "Selenium, Se": {
        "unit": "ug",
        "rda_ai": {"male": _same_across_brackets(55), "female": _same_across_brackets(55)},
        "ul": {"male": _same_across_brackets(400), "female": _same_across_brackets(400)},
    },
    "Copper, Cu": {
        "unit": "mg",
        "rda_ai": {"male": _same_across_brackets(0.9), "female": _same_across_brackets(0.9)},
        "ul": {"male": _same_across_brackets(10), "female": _same_across_brackets(10)},
    },
    "Manganese, Mn": {
        "unit": "mg",
        "rda_ai": {"male": _same_across_brackets(2.3), "female": _same_across_brackets(1.8)},
        "ul": {"male": _same_across_brackets(11), "female": _same_across_brackets(11)},
    },
    "Potassium, K": {
        "unit": "mg",
        "rda_ai": {"male": _same_across_brackets(3400), "female": _same_across_brackets(2600)},
        "ul": {"male": _same_across_brackets(None), "female": _same_across_brackets(None)},
    },
    "Sodium, Na": {
        "unit": "mg",
        "rda_ai": {"male": _same_across_brackets(1500), "female": _same_across_brackets(1500)},
        "ul": {"male": _same_across_brackets(2300), "female": _same_across_brackets(2300)},
    },
}


def get_targets_for(sex: str, age: int) -> dict:
    """
    Resolve DRI daily-target/max-threshold values for a given sex + age.

    Args:
        sex: "male" or "female" (matches USDA/DRI table convention).
        age: age in years.

    Returns:
        {nutrient_name: {"unit": str, "daily_target": float, "max_threshold": float|None}}
    """
    bracket = _age_bracket(age)
    sex_key = sex.lower()
    if sex_key not in ("male", "female"):
        raise ValueError(f"sex must be 'male' or 'female', got: {sex!r}")

    resolved = {}
    for nutrient, info in DRI_TABLE.items():
        resolved[nutrient] = {
            "unit": info["unit"],
            "daily_target": info["rda_ai"][sex_key][bracket],
            "max_threshold": info["ul"][sex_key][bracket],
        }
    return resolved


def _age_bracket(age: int) -> str:
    if age < 19:
        # This table intentionally excludes pediatric DRIs (see module
        # docstring) — fall back to the youngest adult bracket rather than
        # raising, since age collection elsewhere in this app has no lower
        # bound enforced yet.
        return "19-30"
    if age <= 30:
        return "19-30"
    if age <= 50:
        return "31-50"
    if age <= 70:
        return "51-70"
    return "70+"

"""Grouped presets for the micronutrient dashboard cards, and the
canonical nutrient display order used everywhere a food entry's full
nutrient breakdown is shown (diary entries, the Universal Event Contract's
food_entry metadata, recipe/meal/custom-food/pantry detail views).

Per explicit user request: the micronutrient card should NOT dump all ~23
tracked micronutrients into one always-visible card ("don't shove
everything down the user's throat"). Instead:
  - Grouped preset cards (Vitamins / Minerals), swiped between rather than
    all shown at once.
  - A separate "Important to me" card the user builds themselves from any
    tracked nutrient, with its own small starter presets.
  - A full report view (see routers/targets.py's /nutrients endpoint,
    already exists) remains the "everything, no filtering" fallback.

Presets live here (backend), not hardcoded twice in the frontend, so
GET /targets/nutrient-groups is one real endpoint any client (web, a
future mobile app, a Shortcut) can use to build the same grouped UI
without re-deriving the grouping itself — matches the project's
"every capability is a real backend endpoint" requirement.

Separately (2026-08-27): a logged entry's nutrients dict was being handed
back to every caller in whatever order Postgres happened to return
food_log_nutrients rows in (effectively arbitrary, no ORDER BY) — order_
nutrients() below fixes that by sorting into Cronometer's own report
layout: Protein, then Carbohydrate (with its sub-nutrients immediately
after — Fiber/Sugars/Starch), then Fat (with its sub-nutrients —
saturated/mono/poly/trans fat, cholesterol), then Alcohol, then a small
"General" bucket (Water, Caffeine) for real nutrients that aren't a macro,
a vitamin, or a mineral, then Vitamins, then Minerals. This is a display-
order transform only — the flat {name: {value, unit}} shape is untouched,
so nothing that reads a nutrients dict by name (chart metric selection,
nutrient targets, Cronometer sync) needs to change.
"""

# Each macro's "primary" nutrient (the top-level gram total) plus, where
# Cronometer's own reports nest something underneath it, the sub-nutrients
# in the order Cronometer shows them. Amino acids aren't listed under
# Protein — this app has never logged or tracked them anywhere, so there's
# nothing real to order yet.
MACRO_GROUPS = {
    "Protein": {"primary": "Protein", "sub_nutrients": []},
    "Carbohydrate": {
        "primary": "Carbohydrate, by difference",
        "sub_nutrients": ["Fiber, total dietary", "Sugars, total", "Starch"],
    },
    "Fat": {
        "primary": "Total lipid (fat)",
        "sub_nutrients": [
            "Fatty acids, total saturated",
            "Fatty acids, total monounsaturated",
            "Fatty acids, total polyunsaturated",
            "Fatty acids, total trans",
            "Cholesterol",
        ],
    },
    "Alcohol": {"primary": "Alcohol, ethyl", "sub_nutrients": []},
}

# Real, commonly-logged nutrients that aren't a macro, a vitamin, or a
# mineral — Cronometer's own "General" section.
GENERAL_GROUP = ["Water", "Caffeine"]

VITAMIN_GROUP = [
    "Vitamin A, RAE",
    "Vitamin C, total ascorbic acid",
    "Vitamin D (D2 + D3)",
    "Vitamin E (alpha-tocopherol)",
    "Thiamin",
    "Riboflavin",
    "Niacin",
    "Vitamin B-6",
    "Folate, total",
    "Vitamin B-12",
    "Pantothenic acid",
    "Biotin",
    "Choline, total",
]

MINERAL_GROUP = [
    "Calcium, Ca",
    "Phosphorus, P",
    "Magnesium, Mg",
    "Iron, Fe",
    "Zinc, Zn",
    "Selenium, Se",
    "Copper, Cu",
    "Manganese, Mn",
    "Potassium, K",
    "Sodium, Na",
]

# Small starter suggestions for the user-customizable "Important to me"
# card -- NOT the only nutrients allowed there, just sensible defaults
# before a user has picked their own (most commonly-tracked-by-choice
# nutrients: bone health + the two nutrients most people intentionally
# watch for excess, sodium and B-12 for common deficiency concerns).
IMPORTANT_TO_ME_STARTER_PRESETS = {
    "Bone Health": ["Calcium, Ca", "Vitamin D (D2 + D3)", "Magnesium, Mg"],
    "Common Deficiencies": ["Vitamin B-12", "Iron, Fe", "Vitamin D (D2 + D3)"],
    "Sodium Watch": ["Sodium, Na", "Potassium, K"],
}

NUTRIENT_GROUPS = {
    "Vitamins": VITAMIN_GROUP,
    "Minerals": MINERAL_GROUP,
}


def get_nutrient_groups() -> dict:
    """Returns the grouped-preset structure for GET /targets/nutrient-groups."""
    return {
        "groups": NUTRIENT_GROUPS,
        "important_to_me_starter_presets": IMPORTANT_TO_ME_STARTER_PRESETS,
    }


def order_nutrients(nutrients: dict) -> dict:
    """Reorder a flat {nutrient_name: {value, unit}} dict into TrackStack's
    canonical display order (see module docstring). Anything not in any
    known group — an unusual label-scanned name, a CNF nutrient using a
    different naming convention — is appended last, in whatever order it
    was already in. Nothing is ever dropped, just left unclassified."""
    ordered_names: list[str] = []
    seen: set[str] = set()

    for macro in ("Protein", "Carbohydrate", "Fat", "Alcohol"):
        spec = MACRO_GROUPS[macro]
        for name in (spec["primary"], *spec["sub_nutrients"]):
            if name in nutrients and name not in seen:
                ordered_names.append(name)
                seen.add(name)

    for group in (GENERAL_GROUP, VITAMIN_GROUP, MINERAL_GROUP):
        for name in group:
            if name in nutrients and name not in seen:
                ordered_names.append(name)
                seen.add(name)

    for name in nutrients:
        if name not in seen:
            ordered_names.append(name)
            seen.add(name)

    return {name: nutrients[name] for name in ordered_names}

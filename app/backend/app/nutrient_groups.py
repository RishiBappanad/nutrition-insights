"""Grouped presets for the micronutrient dashboard cards.

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
"""

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

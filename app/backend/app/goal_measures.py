"""What a goal can measure, grouped the way a person thinks about it --
Macros, Vitamins, Minerals, Other nutrients, and Vitals -- each with its
standard unit (g / mg / µg / kcal, lb / %).

The groups are derived from the NUTRIENTS a food carries (nutrient_groups
.py's own Vitamin/Mineral/Macro lists), not from the food's category
(fruit, dairy, ...): a goal is asserted on a nutrient total, and the group
is only how the picker and the goals list organize those nutrients.

Static reference data, not user-scoped. describe_measure() goes the other
way: given a stored goal's measure_query, name its group/label/unit for
display.
"""
from typing import Optional

from .nutrient_groups import MACRO_GROUPS, GENERAL_GROUP, VITAMIN_GROUP, MINERAL_GROUP
from .nutrition_targets import nutrient_unit
from .vitals import VITALS, VITAL_OWNER_TYPE

GROUP_MACROS = "Macros"
GROUP_VITAMINS = "Vitamins"
GROUP_MINERALS = "Minerals"
GROUP_OTHER = "Other"
GROUP_VITALS = "Vitals"
GROUP_ORDER = (GROUP_MACROS, GROUP_VITAMINS, GROUP_MINERALS, GROUP_OTHER, GROUP_VITALS)

_DISPLAY_UNITS = {"ug": "µg", "cal": "kcal"}

_LABELS = {
    "Carbohydrate, by difference": "Carbohydrates",
    "Total lipid (fat)": "Fat",
    "Fiber, total dietary": "Fiber",
    "Sugars, total": "Sugars",
    "Fatty acids, total saturated": "Saturated fat",
    "Fatty acids, total monounsaturated": "Monounsaturated fat",
    "Fatty acids, total polyunsaturated": "Polyunsaturated fat",
    "Fatty acids, total trans": "Trans fat",
    "Alcohol, ethyl": "Alcohol",
}


def display_unit(raw_unit: str) -> str:
    return _DISPLAY_UNITS.get(raw_unit, raw_unit)


def nutrient_label(nutrient_name: str) -> str:
    return _LABELS.get(nutrient_name, nutrient_name)


def _macro_nutrients() -> list[str]:
    names: list[str] = []
    for group in MACRO_GROUPS.values():
        if group["primary"] == "Alcohol, ethyl":
            continue  # alcohol is a "macro" in Cronometer's layout, but not one anyone sets a goal shape around
        names.append(group["primary"])
        names.extend(group["sub_nutrients"])
    return names


_NUTRIENT_GROUP = {
    **{n: GROUP_MACROS for n in _macro_nutrients()},
    **{n: GROUP_VITAMINS for n in VITAMIN_GROUP},
    **{n: GROUP_MINERALS for n in MINERAL_GROUP},
    **{n: GROUP_OTHER for n in [*GENERAL_GROUP, "Alcohol, ethyl"]},
}


def nutrient_group(nutrient_name: str) -> str:
    return _NUTRIENT_GROUP.get(nutrient_name, GROUP_OTHER)


def list_measures() -> list[dict]:
    """[{field, kind, group, label, unit, vital?}] -- `field` is the
    GoalQuery measureField ("nutrient:<Name>", None = calories); vitals
    carry `vital` (their key) instead, since they aren't a nutrient_facts
    measure."""
    measures = [{"field": None, "kind": "nutrient", "group": GROUP_MACROS, "label": "Calories", "unit": "kcal"}]
    for name, group in _NUTRIENT_GROUP.items():
        measures.append({
            "field": f"nutrient:{name}", "kind": "nutrient", "group": group,
            "label": nutrient_label(name), "unit": display_unit(nutrient_unit(name)),
        })
    for key, info in VITALS.items():
        measures.append({"field": None, "kind": "vital", "vital": key, "group": GROUP_VITALS, "label": info["label"], "unit": info["unit"]})
    order = {g: i for i, g in enumerate(GROUP_ORDER)}
    return sorted(measures, key=lambda m: order[m["group"]])  # stable: keeps each group's own order


def describe_measure(measure_query: dict) -> dict:
    """{group, label, unit, vital} for a stored measure_query."""
    owner = next((f for f in measure_query.get("filters", []) if f.get("field") == "owner_type" and f.get("operator") == "eq"), None)
    if owner and owner.get("value") == VITAL_OWNER_TYPE:
        category = next((f.get("value") for f in measure_query["filters"] if f.get("field") == "category" and f.get("operator") == "eq"), None)
        info = VITALS.get(category)
        if info:
            return {"group": GROUP_VITALS, "label": info["label"], "unit": info["unit"], "vital": category}
        return {"group": GROUP_VITALS, "label": "Vital", "unit": "", "vital": None}
    field: Optional[str] = measure_query.get("measureField")
    if field:
        name = field[len("nutrient:"):]
        return {"group": nutrient_group(name), "label": nutrient_label(name), "unit": display_unit(nutrient_unit(name)), "vital": None}
    return {"group": GROUP_MACROS, "label": "Calories", "unit": "kcal", "vital": None}

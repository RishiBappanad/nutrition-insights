"""
Portion/weight scaling: given a food's nutrient profile at a known
reference amount, compute the proportionally-scaled nutrients for a
different amount (e.g. 100g reference -> 250g requested = 2.5x every
nutrient).

This did not exist anywhere in the codebase before this module — verified
by search: `serving_size` was stored as a plain field on food_log/
pantry_items but nothing ever multiplied nutrient values by it. Every
caller (food logging, pantry consume, recipes, meals) was implicitly
relying on the frontend (which doesn't exist yet) to have already done
this math before submitting a request. This module is the single,
shared implementation so no caller reimplements the scaling formula.

Two scaling modes, matching how food reference data actually varies:
- Gram-based: the food's serving is a real gram weight (USDA/CNF usually
  give a `serving_size` + `serving_unit` like "100 g" or "1 medium
  (118g)" — the gram number is what matters, the label is display only).
  Scaling is a straight ratio: to_grams / from_grams.
- Multiple-based: the reference has no reliable gram equivalence (e.g. a
  custom food entered as "1 jar" with no weight) — scaling here means
  "N of this same reference serving," a simple multiplier, not a gram
  conversion.
"""
from typing import Optional


def scale_nutrients(nutrients: dict, factor: float) -> dict:
    """Scale every {name: {value, unit}} entry in `nutrients` by `factor`.
    Units are never converted or changed — only the value is scaled, since
    a unit (mg, g, ug) doesn't change based on how much food you're
    scaling it for."""
    if factor < 0:
        raise ValueError("scaling factor must not be negative")
    scaled = {}
    for name, info in nutrients.items():
        if not isinstance(info, dict) or info.get("value") is None:
            continue
        try:
            value = float(info["value"])
        except (TypeError, ValueError):
            continue
        scaled[name] = {"value": round(value * factor, 6), "unit": info.get("unit", "")}
    return scaled


def scale_macros(macros: dict, factor: float) -> dict:
    """Scale the single top-level `calories` field by `factor`. Kept
    separate from scale_nutrients since macros aren't a
    {name: {value, unit}} dict — they're a flat top-level field on
    food_log/pantry_items, a different shape needing its own scaler.
    Protein/carbs/fat/fiber are NOT macro fields here — calories is the
    sole top-level scalar (TrackStack's "amount" field for this tracker);
    every other nutrient lives in `nutrients` (e.g. "Protein",
    "Carbohydrate, by difference", "Total lipid (fat)", "Fiber, total
    dietary") and is scaled by scale_nutrients instead."""
    if factor < 0:
        raise ValueError("scaling factor must not be negative")
    return {
        key: round(float(macros.get(key, 0) or 0) * factor, 4)
        for key in ("calories",)
    }


def gram_based_factor(from_grams: float, to_grams: float) -> float:
    """Ratio for scaling a food whose reference amount is a known gram
    weight. Raises if from_grams is not positive — a zero/negative
    reference weight has no valid ratio, this is a real data problem to
    surface, not something to silently divide-by-zero or clamp."""
    if from_grams <= 0:
        raise ValueError(f"from_grams must be positive, got {from_grams}")
    if to_grams < 0:
        raise ValueError(f"to_grams must not be negative, got {to_grams}")
    return to_grams / from_grams


def multiple_based_factor(servings_requested: float) -> float:
    """Factor for scaling a food with no gram reference — just "N of the
    same reference serving." This is the identity case: the factor IS the
    requested serving count, since the reference itself represents 1x."""
    if servings_requested < 0:
        raise ValueError(f"servings_requested must not be negative, got {servings_requested}")
    return servings_requested


def scale_food_entry(
    macros: dict,
    nutrients: dict,
    mode: str,
    from_grams: Optional[float] = None,
    to_grams: Optional[float] = None,
    servings_requested: Optional[float] = None,
) -> dict:
    """
    High-level entry point combining macro + nutrient scaling for one of
    the two modes. Returns {"macros": {...}, "nutrients": {...}, "factor": float}.

    mode="grams": requires from_grams (the food's reference weight) and
        to_grams (the amount actually being logged).
    mode="multiple": requires servings_requested (e.g. "2" for two of the
        reference serving, with no gram conversion involved at all).
    """
    if mode == "grams":
        if from_grams is None or to_grams is None:
            raise ValueError("mode='grams' requires from_grams and to_grams")
        factor = gram_based_factor(from_grams, to_grams)
    elif mode == "multiple":
        if servings_requested is None:
            raise ValueError("mode='multiple' requires servings_requested")
        factor = multiple_based_factor(servings_requested)
    else:
        raise ValueError(f"unknown scaling mode: {mode!r}")

    return {
        "macros": scale_macros(macros, factor),
        "nutrients": scale_nutrients(nutrients, factor),
        "factor": factor,
    }

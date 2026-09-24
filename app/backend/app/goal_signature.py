"""Duplicate detection for goals: two goals conflict when they'd assert
the same thing about the same measure -- e.g. two daily protein floors --
regardless of the amount each one names (the amount is exactly what would
disagree).

The signature is (canonical measure query, reference kind, direction,
severity):
  - direction folds the comparator into floor (gte) / ceiling (lte) /
    target (eq, within_tolerance_percent), so a protein floor and a protein
    ceiling coexist but two floors don't.
  - severity stays in, because a soft "warning" tier plus a hard "target"
    tier on the same measure is a supported, deliberate pairing.
  - a constant reference collapses to one tag (its amount is what a
    duplicate would differ in); a computed baseline keeps its full query,
    since "vs. trailing 7 days" and "vs. trailing 3 months" are different
    goals.
"""
import json
from typing import Optional

_DIRECTION = {"gte": "floor", "lte": "ceiling"}


def _canonical_query(query: dict) -> str:
    normalized = {**query, "filters": sorted(json.dumps(f, sort_keys=True) for f in query.get("filters", []))}
    return json.dumps(normalized, sort_keys=True)


def goal_signature(measure_query: dict, reference_query: Optional[dict], comparator: str, severity: str) -> str:
    return json.dumps([
        _canonical_query(measure_query),
        "const" if reference_query is None else _canonical_query(reference_query),
        _DIRECTION.get(comparator, "target"),
        severity,
    ])


def signature_of_goal(goal: dict) -> str:
    """`goal` is a decoded goals row (measure_query/reference_query dicts)."""
    return goal_signature(goal["measure_query"], goal["reference_query"], goal["comparator"], goal["severity"])

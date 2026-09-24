"""Vitals -- point-in-time body measurements (weight, body fat) that Goals
can target the same way they target nutrients.

There is no separate vitals table: readings already live in
`daily_nutrition` (one row per user/date/metric -- the same rows the
Charts page and Cronometer's biometrics sync use, e.g. "Weight (lbs)"),
so a manual log, a synced reading, and a chart all see one series. What
this module adds is the bridge Goals needs: every change to a vital's
daily_nutrition row also appends a `vital_created`/`vital_updated`
domain_event (owner_type "vital", owner_id = that row's id, category =
the vital's key, amount = the reading), which is exactly the shape the
Goal Query interpreter already aggregates -- no second measure source,
and event-triggered goal_met/goal_exceeded crossings work for free.

Adding another vital (waist, resting heart rate) is one entry in VITALS.
"""
from typing import Optional

VITAL_OWNER_TYPE = "vital"

# key -> daily_nutrition.metric name, display label, display unit.
VITALS = {
    "weight": {"metric": "Weight (lbs)", "label": "Weight", "unit": "lb"},
    "body_fat": {"metric": "Body Fat (%)", "label": "Body fat", "unit": "%"},
}

_KEY_BY_METRIC = {v["metric"]: k for k, v in VITALS.items()}


def vital_key_for_metric(metric: str) -> Optional[str]:
    return _KEY_BY_METRIC.get(metric)


def is_valid_vital(key) -> bool:
    return isinstance(key, str) and key in VITALS

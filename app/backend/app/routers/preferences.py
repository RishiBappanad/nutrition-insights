"""User display preferences: configurable colors for the dashboard's macro
chart segments and micronutrient status bars, plus the micronutrient
"sufficient" percentage threshold. A real backend endpoint (not
frontend-only localStorage) per this project's API-first requirement — so
preferences are consistent across devices and settable via a bare API
call, not tied to one browser's storage."""
import json
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from ..routers.auth import get_current_user
from ..db import get_pool

router = APIRouter()

HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

# Every color-coded element across the app that a user can currently see,
# with a documented default matching what was hardcoded before this
# feature existed:
#   - macro_*: dashboard's MacroCard pie/legend segments (was: fixed CSS
#     theme chart-1..4 vars)
#   - nutrient_*: dashboard's MicronutrientCard status bars (was: fixed
#     Tailwind color classes)
#   - chart_line_1/2/3: pages/charts.jsx's up-to-3-selected-metric line
#     palette (was: a hardcoded 3-item hsl() array, colors[idx % 3])
#   - lift_scatter: pages/lift-insights.jsx's ORM scatter plot dot color
#     (was: a hardcoded hsl() literal)
# One source of truth here (not hardcoded separately per-page in the
# frontend) — the frontend fetches this via GET and only falls back to
# these same literal values if the request fails entirely (e.g. offline).
DEFAULT_COLORS = {
    "macro_protein": "#3b82f6",
    "macro_carbs": "#10b981",
    "macro_fat": "#f59e0b",
    "macro_alcohol": "#8b5cf6",
    "nutrient_insufficient": "#6b7280",
    "nutrient_sufficient": "#10b981",
    "nutrient_excess": "#ef4444",
    "chart_line_1": "#2d5344",
    "chart_line_2": "#b2804d",
    "chart_line_3": "#526d7a",
    "lift_scatter": "#2d5344",
}
DEFAULT_SUFFICIENCY_THRESHOLD_PCT = 90.0
DEFAULT_UNIT_SYSTEM = "imperial"
DEFAULT_MACRO_CHART_STYLE = "pie"
VALID_UNIT_SYSTEMS = {"metric", "imperial"}
VALID_MACRO_CHART_STYLES = {"pie", "bar"}


class PreferencesRequest(BaseModel):
    colors: dict = {}
    sufficiency_threshold_pct: Optional[float] = None
    unit_system: Optional[str] = None
    macro_chart_style: Optional[str] = None

    @field_validator("colors")
    @classmethod
    def _validate_colors(cls, v: dict) -> dict:
        for key, value in v.items():
            if key not in DEFAULT_COLORS:
                raise ValueError(f"unknown color key: {key!r}. Valid keys: {sorted(DEFAULT_COLORS)}")
            if not isinstance(value, str) or not HEX_COLOR_RE.match(value):
                raise ValueError(f"color for {key!r} must be a 6-digit hex string like '#3b82f6', got {value!r}")
        return v

    @field_validator("sufficiency_threshold_pct")
    @classmethod
    def _validate_threshold(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and not (0 < v <= 100):
            raise ValueError("sufficiency_threshold_pct must be between 0 and 100")
        return v

    @field_validator("unit_system")
    @classmethod
    def _validate_unit_system(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in VALID_UNIT_SYSTEMS:
            raise ValueError(f"unit_system must be one of {sorted(VALID_UNIT_SYSTEMS)}")
        return v

    @field_validator("macro_chart_style")
    @classmethod
    def _validate_macro_chart_style(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in VALID_MACRO_CHART_STYLES:
            raise ValueError(f"macro_chart_style must be one of {sorted(VALID_MACRO_CHART_STYLES)}")
        return v


@router.get("")
async def get_preferences(user_id: int = Depends(get_current_user)):
    """Always returns a complete colors map (stored overrides merged over
    defaults) and resolved scalar preferences — the frontend never needs
    its own fallback logic for a partially-set preferences row."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM user_preferences WHERE user_id = $1", user_id)

    stored_colors = json.loads(row["colors_json"]) if row and row["colors_json"] else {}
    colors = {**DEFAULT_COLORS, **stored_colors}
    threshold = row["sufficiency_threshold_pct"] if row and row["sufficiency_threshold_pct"] is not None else DEFAULT_SUFFICIENCY_THRESHOLD_PCT
    unit_system = row["unit_system"] if row and row["unit_system"] else DEFAULT_UNIT_SYSTEM
    macro_chart_style = row["macro_chart_style"] if row and row["macro_chart_style"] else DEFAULT_MACRO_CHART_STYLE

    return {
        "colors": colors,
        "sufficiency_threshold_pct": threshold,
        "unit_system": unit_system,
        "macro_chart_style": macro_chart_style,
    }


@router.put("")
async def set_preferences(req: PreferencesRequest, user_id: int = Depends(get_current_user)):
    """Partial update — only the fields actually present in the request
    are changed; omitted fields keep their current stored value (or
    default, if never set). Mirrors nutrition_targets.py's
    is_custom-preserving pattern: a user setting one preference shouldn't
    reset every other preference back to default."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow("SELECT * FROM user_preferences WHERE user_id = $1", user_id)
        current_colors = json.loads(existing["colors_json"]) if existing and existing["colors_json"] else {}
        merged_colors = {**current_colors, **req.colors}

        threshold = req.sufficiency_threshold_pct
        if threshold is None and existing:
            threshold = existing["sufficiency_threshold_pct"]

        unit_system = req.unit_system
        if unit_system is None and existing:
            unit_system = existing["unit_system"]

        macro_chart_style = req.macro_chart_style
        if macro_chart_style is None and existing:
            macro_chart_style = existing["macro_chart_style"]

        await conn.execute(
            """INSERT INTO user_preferences (user_id, colors_json, sufficiency_threshold_pct, unit_system, macro_chart_style, updated_at)
               VALUES ($1, $2, $3, $4, $5, now())
               ON CONFLICT (user_id) DO UPDATE SET
                   colors_json = EXCLUDED.colors_json,
                   sufficiency_threshold_pct = EXCLUDED.sufficiency_threshold_pct,
                   unit_system = EXCLUDED.unit_system,
                   macro_chart_style = EXCLUDED.macro_chart_style,
                   updated_at = now()""",
            user_id, json.dumps(merged_colors), threshold, unit_system, macro_chart_style,
        )

    return {
        "colors": {**DEFAULT_COLORS, **merged_colors},
        "sufficiency_threshold_pct": threshold or DEFAULT_SUFFICIENCY_THRESHOLD_PCT,
        "unit_system": unit_system or DEFAULT_UNIT_SYSTEM,
        "macro_chart_style": macro_chart_style or DEFAULT_MACRO_CHART_STYLE,
    }


@router.delete("")
async def reset_preferences(user_id: int = Depends(get_current_user)):
    """Reset everything back to defaults."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM user_preferences WHERE user_id = $1", user_id)
    return {
        "colors": DEFAULT_COLORS,
        "sufficiency_threshold_pct": DEFAULT_SUFFICIENCY_THRESHOLD_PCT,
        "unit_system": DEFAULT_UNIT_SYSTEM,
        "macro_chart_style": DEFAULT_MACRO_CHART_STYLE,
    }

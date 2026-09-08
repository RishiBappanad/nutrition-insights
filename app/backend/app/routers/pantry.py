"""Pantry/fridge inventory API. See nutrition-diary-design.md "Pantry /
fridge schema" section for the full design rationale — this implements
that spec exactly: tracking_mode ('countable'|'bulk'|'single')
discriminates how POST /consume affects remaining_servings, rather than
three separate item types.

Nutrition is stored PER serving_size/serving_unit (the same "reference
amount, scale by count" convention custom_foods uses) so that consuming
or removing an item can reflect real nutrition in the diary without the
caller re-entering it every time — this was a real gap fixed per user
request (previously a pantry item stored zero nutrition and /consume
silently logged 0 macros unless a caller happened to resupply them,
which the frontend never did)."""
from datetime import date as date_cls, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..routers.auth import get_current_user
from ..db.pantry import query as pantry_query
from ..food_category import FoodCategory

router = APIRouter()

VALID_MODES = {"countable", "bulk", "single"}


class PantryItemRequest(BaseModel):
    food_name: str
    source: Optional[str] = None
    source_id: Optional[str] = None
    # Carried through as-is from whatever the caller has (e.g. a search
    # result's already-resolved category, or a custom food's) -- a
    # pantry item is a single item, not a composite, so there's no
    # dominant-by-calories default to compute here.
    category: Optional[FoodCategory] = None
    serving_size: float = 1.0
    serving_unit: str = "serving"
    tracking_mode: str = "countable"
    remaining_servings: Optional[float] = None
    expiration_date: Optional[str] = None
    # Nutrition PER serving_size/serving_unit (e.g. if serving_size=100,
    # serving_unit="g", these are the per-100g values) -- stored once at
    # add time so /consume and /remove never need the caller to resupply
    # nutrition data. `calories` is the sole top-level numeric field;
    # protein/carbs/fat/fiber belong in nutrients under their standard
    # USDA names.
    calories: float = 0
    nutrients: dict = {}


class PantryItemUpdateRequest(BaseModel):
    remaining_servings: Optional[float] = None
    expiration_date: Optional[str] = None
    tracking_mode: Optional[str] = None


class ConsumeRequest(BaseModel):
    servings: float
    date: str
    meal: str = "Snack"


class RemoveServingsRequest(BaseModel):
    """For sharing, spoilage of part of a countable stack, etc. -- take
    servings out of pantry WITHOUT logging to the diary (distinct from
    /consume, which always logs). Only meaningful for countable items;
    single and bulk items have their own no-diary removal actions
    (DELETE /{id} and POST /{id}/finish respectively, both pre-existing)."""
    servings: float


def _validate_mode(mode: str):
    if mode not in VALID_MODES:
        raise HTTPException(status_code=400, detail=f"tracking_mode must be one of {sorted(VALID_MODES)}")


@router.post("")
async def add_pantry_item(req: PantryItemRequest, user_id: int = Depends(get_current_user)):
    _validate_mode(req.tracking_mode)
    if req.tracking_mode == "countable" and req.remaining_servings is None:
        raise HTTPException(status_code=400, detail="countable items require remaining_servings")

    remaining = req.remaining_servings
    if req.tracking_mode == "single":
        remaining = 1.0  # single items are always exactly one, regardless of what was passed
    elif req.tracking_mode == "bulk":
        remaining = None  # bulk items don't track a count at all

    item_id = await pantry_query.create_pantry_item(
        user_id, req.food_name, req.source, req.source_id, req.category, req.serving_size,
        req.serving_unit, req.tracking_mode, remaining, req.expiration_date,
        req.calories, req.nutrients,
    )
    return {"status": "added", "id": item_id}


@router.get("")
async def list_pantry_items(user_id: int = Depends(get_current_user)):
    """Excludes finished items — matches the design doc's 'gone means
    gone' behavior for bulk items marked finished."""
    rows = await pantry_query.list_pantry_items(user_id)
    return {"items": [_row_to_item(r) for r in rows]}


@router.get("/expiring")
async def get_expiring_items(days: int = Query(7, ge=0), user_id: int = Depends(get_current_user)):
    """Items expiring within `days` days (default 7), for a future
    dashboard/notification surface. Excludes items with no expiration
    date set — those never 'expire' by definition."""
    cutoff = (date_cls.today() + timedelta(days=days)).isoformat()
    today = date_cls.today().isoformat()
    rows = await pantry_query.list_expiring_items(user_id, cutoff)
    return {
        "items": [_row_to_item(r) for r in rows],
        "as_of": today,
        "within_days": days,
    }


@router.patch("/{item_id}")
async def update_pantry_item(item_id: int, req: PantryItemUpdateRequest, user_id: int = Depends(get_current_user)):
    existing = await pantry_query.get_pantry_item_for_update(item_id, user_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Pantry item not found")

    if req.tracking_mode is not None:
        _validate_mode(req.tracking_mode)

    await pantry_query.update_pantry_item(
        item_id, user_id, req.remaining_servings, req.expiration_date, req.tracking_mode,
    )
    return {"status": "updated"}


@router.delete("/{item_id}")
async def delete_pantry_item(item_id: int, user_id: int = Depends(get_current_user)):
    """Remove without logging to diary (expired, thrown away, bought by
    mistake) — distinct from /consume, which logs first. Deletes
    pantry_items FIRST (its WHERE clause is the ownership check — there's
    no separate SELECT before it), then only cleans up nutrient_facts if
    that actually removed a row: nutrient_facts has no user_id column of
    its own, so calling delete_nutrient_facts for an item_id that turned
    out to belong to a different user (or not exist) would wipe that
    other user's row instead of a no-op."""
    await pantry_query.delete_pantry_item(item_id, user_id)
    return {"status": "deleted"}


@router.post("/{item_id}/finish")
async def finish_pantry_item(item_id: int, user_id: int = Depends(get_current_user)):
    """Mark a bulk item as used up. Deletes the row outright (matches the
    design doc's 'gone means gone' reasoning — no soft-delete state to
    manage for a finished item). Same delete-then-conditionally-clean-up
    ordering as delete_pantry_item above, for the same reason."""
    deleted = await pantry_query.finish_pantry_item(item_id, user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Pantry item not found")
    return {"status": "finished"}


@router.post("/{item_id}/consume")
async def consume_pantry_item(item_id: int, req: ConsumeRequest, user_id: int = Depends(get_current_user)):
    """
    Atomic pantry-to-diary action: creates a real food_log entry (+
    food_log_nutrients) for the consumed servings, then updates/removes
    the pantry item based on tracking_mode — one backend call, not two
    sequenced frontend requests. Nutrition is read from the pantry item
    itself (stored at add-time, per serving_size/serving_unit) and scaled
    by the number of servings consumed — the caller no longer needs to
    resupply calories/nutrients at consume time. See design doc's
    "Consumption flow" section for the exact per-mode behavior:
      - countable: decrements remaining_servings; deletes the row if it
        would hit <= 0.
      - bulk: no quantity change (call /finish separately when it's used up).
      - single: any consumption deletes the row — there's no partial state.
    """
    if req.servings <= 0:
        raise HTTPException(status_code=400, detail="servings must be positive")

    result = await pantry_query.consume_pantry_item(item_id, user_id, req.servings, req.date, req.meal)
    if result.get("error") == "not_found":
        raise HTTPException(status_code=404, detail="Pantry item not found")
    if result.get("error") == "insufficient":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot consume {req.servings} servings — only {result['remaining']} remaining",
        )
    return {"status": "logged", "food_log_id": result["food_log_id"], "pantry_status": result["pantry_status"]}


@router.post("/{item_id}/remove")
async def remove_pantry_servings(item_id: int, req: RemoveServingsRequest, user_id: int = Depends(get_current_user)):
    """Take servings out of a countable pantry item WITHOUT logging to the
    diary — e.g. shared with someone else, spoiled, given away. Mirrors
    /consume's decrement/delete-at-zero logic exactly, just skips the
    food_log insert. Only valid for tracking_mode='countable': single
    items already have DELETE /{id} for no-diary removal, and bulk items
    already have POST /{id}/finish — both pre-existing, unchanged."""
    if req.servings <= 0:
        raise HTTPException(status_code=400, detail="servings must be positive")

    result = await pantry_query.remove_pantry_servings(item_id, user_id, req.servings)
    if result.get("error") == "not_found":
        raise HTTPException(status_code=404, detail="Pantry item not found")
    if result.get("error") == "not_countable":
        raise HTTPException(
            status_code=400,
            detail="Only countable items support partial removal — use DELETE /{id} for single "
                   "items or POST /{id}/finish for bulk items",
        )
    if result.get("error") == "insufficient":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot remove {req.servings} servings — only {result['remaining']} remaining",
        )
    return {"status": "removed_no_log", "pantry_status": result["pantry_status"]}


def _row_to_item(r) -> dict:
    return {
        "id": r["id"],
        "food_name": r["food_name"],
        "source": r["source"],
        "source_id": r["source_id"],
        "category": r["category"],
        "serving_size": r["serving_size"],
        "serving_unit": r["serving_unit"],
        "tracking_mode": r["tracking_mode"],
        "remaining_servings": r["remaining_servings"],
        "is_finished": r["is_finished"],
        "expiration_date": r["expiration_date"],
        "calories": r["calories"],
        "added_at": r["added_at"].isoformat(),
        "updated_at": r["updated_at"].isoformat(),
    }

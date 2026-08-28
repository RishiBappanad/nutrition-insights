"""Single shared table + read/write/delete helpers for "named sub-attributes
of a loggable item" — nutrients today, whatever comes next tomorrow. Any
tracked object that needs a flexible {name: {value, unit}} breakdown (a
food_log entry, a pantry item, a custom food, a recipe/meal item, and
whatever's added later) references the ONE nutrient_facts table (see
db.py) by (owner_type, owner_id) instead of getting its own hand-rolled
X_nutrients table plus its own duplicate read/write SQL.

This directly replaces food_log_nutrients, pantry_item_nutrients,
custom_food_nutrients, recipe_item_nutrients, and meal_item_nutrients
(migrated into nutrient_facts in db.py's init_db, 2026-08-27) — 5
byte-for-byte identical tables that had 6 independent hand-rolled read
implementations and 2 independent (one a hand-duplicate) write
implementations scattered across 5 router files. That duplication was the
root cause of a single nutrient-related change needing to touch 5+ files:
adding a 6th "loggable item with named sub-attributes" concept used to
mean a new table plus new read/write SQL; now it means one new
OWNER_TYPES entry.

owner_type is a plain string discriminator, not a foreign key — Postgres
has no polymorphic FK across 5 different parent tables in one column.
Referential integrity for this table is therefore enforced in application
code, not the schema: every DELETE of an owning row (or a bulk replace of
its items, e.g. re-saving a whole recipe) MUST also call
delete_nutrient_facts()/delete_nutrient_facts_bulk() for it, or its
nutrient_facts rows become permanently orphaned (harmless to correctness
— nothing reads a row for an owner_id that no longer exists anywhere —
but a real, avoidable storage leak).
"""
from typing import Iterable

# owner_type -> the table it references. Documentation only (no FK is
# possible across 5 different parent tables in one column) — kept here so
# adding a new owner_type is a one-line addition, not a new table.
OWNER_TYPES = {
    "food_log": "food_log",
    "pantry_item": "pantry_items",
    "custom_food": "custom_foods",
    "recipe_item": "recipe_items",
    "meal_item": "meal_items",
}


def _nutrients_to_rows(owner_type: str, owner_id: int, nutrients: dict) -> list[tuple]:
    """Normalize a {name: {value, unit}} dict into nutrient_facts rows,
    silently dropping any entry missing a numeric value — this is the ONE
    normalization implementation now (previously duplicated verbatim
    between food_entry_contract.py and routers/custom_foods.py, and
    reimplemented slightly differently again in routers/meals.py and
    routers/recipes.py)."""
    rows = []
    for name, info in (nutrients or {}).items():
        if not isinstance(info, dict):
            continue
        value = info.get("value")
        if value is None:
            continue
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        rows.append((owner_type, owner_id, name, value, info.get("unit", "")))
    return rows


async def write_nutrients(conn, owner_type: str, owner_id: int, nutrients: dict) -> None:
    """Insert (or update, on conflict) one owner's full nutrient
    breakdown. Does NOT clear existing rows first — a caller REPLACING an
    item's nutrients (an edit) should call delete_nutrient_facts() first;
    a caller creating a brand-new item never needs to."""
    rows = _nutrients_to_rows(owner_type, owner_id, nutrients)
    if not rows:
        return
    await conn.executemany(
        """INSERT INTO nutrient_facts (owner_type, owner_id, nutrient_name, value, unit)
           VALUES ($1, $2, $3, $4, $5)
           ON CONFLICT (owner_type, owner_id, nutrient_name)
           DO UPDATE SET value = EXCLUDED.value, unit = EXCLUDED.unit""",
        rows,
    )


async def read_nutrients_bulk(conn, owner_type: str, owner_ids: Iterable[int]) -> dict[int, dict]:
    """Read every owner's nutrients in one query. Returns
    {owner_id: {name: {value, unit}}}, each already in TrackStack's
    canonical display order (see nutrient_groups.order_nutrients) — every
    caller of this function gets ordered nutrients for free, rather than
    needing to remember to call order_nutrients() itself."""
    from .nutrient_groups import order_nutrients

    owner_ids = list(owner_ids)
    if not owner_ids:
        return {}
    rows = await conn.fetch(
        "SELECT owner_id, nutrient_name, value, unit FROM nutrient_facts "
        "WHERE owner_type = $1 AND owner_id = ANY($2::int[])",
        owner_type, owner_ids,
    )
    by_owner: dict[int, dict] = {}
    for r in rows:
        by_owner.setdefault(r["owner_id"], {})[r["nutrient_name"]] = {
            "value": r["value"], "unit": r["unit"],
        }
    return {owner_id: order_nutrients(nutrients) for owner_id, nutrients in by_owner.items()}


async def read_nutrients(conn, owner_type: str, owner_id: int) -> dict:
    """Read one owner's nutrients. Thin wrapper over read_nutrients_bulk
    for the common single-id case."""
    return (await read_nutrients_bulk(conn, owner_type, [owner_id])).get(owner_id, {})


async def delete_nutrient_facts(conn, owner_type: str, owner_id: int) -> None:
    """Delete one owner's nutrient rows. Call this explicitly wherever the
    owning row itself is deleted, or its items are being replaced —
    nutrient_facts has no FK to cascade automatically (see module
    docstring)."""
    await conn.execute(
        "DELETE FROM nutrient_facts WHERE owner_type = $1 AND owner_id = $2",
        owner_type, owner_id,
    )


async def delete_nutrient_facts_bulk(conn, owner_type: str, owner_ids: Iterable[int]) -> None:
    """Bulk version of delete_nutrient_facts, for clearing every item
    under a parent being deleted/replaced in one query (e.g. every
    recipe_item under a recipe, before re-inserting the edited list)."""
    owner_ids = list(owner_ids)
    if not owner_ids:
        return
    await conn.execute(
        "DELETE FROM nutrient_facts WHERE owner_type = $1 AND owner_id = ANY($2::int[])",
        owner_type, owner_ids,
    )

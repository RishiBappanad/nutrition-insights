import json

from .. import get_pool, insert_returning, update_with_ownership_returning, delete_with_ownership_returning
from ...nutrient_facts import (
    write_nutrients, read_nutrients_bulk, delete_nutrient_facts, delete_nutrient_facts_bulk,
)
from ...domain_events import log_domain_event

# resolve_category() itself (business logic: what category value to
# store) deliberately stays a router-side concern, called by routers/meals.py
# before it calls create_meal/get_meal_for_update below -- this module only
# ever receives an already-resolved category, never computes one.


async def _save_items(conn, meal_id: int, items) -> None:
    for item in items:
        item_id = await conn.fetchval(
            """INSERT INTO meal_items (meal_id, food_name, source, source_id, category, serving_size, serving_unit,
                   calories, nutrients_json)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
               RETURNING id""",
            meal_id, item.food_name, item.source, item.source_id, item.category,
            item.serving_size, item.serving_unit, item.calories, json.dumps(item.nutrients),
        )
        await write_nutrients(conn, "meal_item", item_id, item.nutrients)


async def create_meal(user_id: int, name: str, resolved_category, category_is_custom, items) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            meal_id = await insert_returning(conn, "meals", {
                "user_id": user_id, "name": name, "category": resolved_category,
                "category_is_custom": category_is_custom,
            })
            await _save_items(conn, meal_id, items)
            await log_domain_event(
                conn, user_id, "meal", meal_id, "created",
                category=resolved_category, label=name, metadata={"item_count": len(items)},
            )
    return meal_id


async def list_meals(user_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch("SELECT id, name, category FROM meals WHERE user_id = $1 ORDER BY name", user_id)


async def get_meal_with_items(conn, meal_id: int, user_id: int):
    meal = await conn.fetchrow("SELECT * FROM meals WHERE id = $1 AND user_id = $2", meal_id, user_id)
    if meal is None:
        return None, []
    item_rows = await conn.fetch("SELECT * FROM meal_items WHERE meal_id = $1 ORDER BY id", meal_id)
    item_ids = [r["id"] for r in item_rows]
    nutrients_by_item = await read_nutrients_bulk(conn, "meal_item", item_ids)
    items = []
    for r in item_rows:
        items.append({
            "id": r["id"],
            "food_name": r["food_name"],
            "source": r["source"],
            "source_id": r["source_id"],
            "category": r["category"],
            "serving_size": r["serving_size"],
            "serving_unit": r["serving_unit"],
            "calories": r["calories"],
            "nutrients": nutrients_by_item.get(r["id"], {}),
        })
    return meal, items


async def get_meal_with_items_new_conn(meal_id: int, user_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await get_meal_with_items(conn, meal_id, user_id)


async def get_meal_for_update(meal_id: int, user_id: int):
    """The pre-update existence/category check used by resolve_category()'s
    'preserve an earlier custom override' behavior -- a separate
    acquisition from update_meal below (was one connection in the
    original; see commit message for why this was split)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT id, category, category_is_custom FROM meals WHERE id = $1 AND user_id = $2", meal_id, user_id
        )


async def update_meal(meal_id: int, user_id: int, name: str, resolved_category, category_is_custom, items) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            updated = await update_with_ownership_returning(
                conn, "meals", meal_id, user_id,
                {"name": name, "category": resolved_category, "category_is_custom": category_is_custom},
                ["name", "category"],
            )
            old_item_ids = [r["id"] for r in await conn.fetch(
                "SELECT id FROM meal_items WHERE meal_id = $1", meal_id
            )]
            await delete_nutrient_facts_bulk(conn, "meal_item", old_item_ids)
            await conn.execute("DELETE FROM meal_items WHERE meal_id = $1", meal_id)
            await _save_items(conn, meal_id, items)
            if updated:
                await log_domain_event(
                    conn, user_id, "meal", meal_id, "updated",
                    category=updated["category"], label=updated["name"], metadata={"item_count": len(items)},
                )


async def delete_meal(meal_id: int, user_id: int) -> None:
    """meal_items has ON DELETE CASCADE to meals, but nutrient_facts has
    no FK at all (see app/nutrient_facts.py), so those rows are cleared
    explicitly first -- ownership-scoped via the join below, not a bare
    meal_id lookup (see original inline comment: without the meals.user_id
    check, a caller could trigger cleanup of another user's meal_item
    nutrient_facts rows even though the meals DELETE itself is correctly
    scoped and would no-op)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            item_ids = [r["id"] for r in await conn.fetch(
                """SELECT mi.id FROM meal_items mi
                   JOIN meals m ON m.id = mi.meal_id
                   WHERE mi.meal_id = $1 AND m.user_id = $2""",
                meal_id, user_id,
            )]
            await delete_nutrient_facts_bulk(conn, "meal_item", item_ids)
            deleted = await delete_with_ownership_returning(conn, "meals", meal_id, user_id, ["name", "category"])
            if deleted:
                await log_domain_event(
                    conn, user_id, "meal", meal_id, "deleted",
                    category=deleted["category"], label=deleted["name"],
                )


async def insert_combined_meal_log(
    user_id: int, date: str, meal_label: str, meal_name: str, meal_id_str: str, category, calories: float, nutrients: dict,
) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            food_log_id = await insert_returning(conn, "food_log", {
                "user_id": user_id, "date": date, "meal": meal_label, "food_name": meal_name,
                "source": "meal", "source_id": meal_id_str, "category": category,
                "serving_size": 1, "serving_unit": "meal", "calories": calories, "nutrients_json": json.dumps(nutrients),
            })
            await write_nutrients(conn, "food_log", food_log_id, nutrients)
            await log_domain_event(
                conn, user_id, "food_log", food_log_id, "created",
                category=category, amount=calories, label=meal_name, source="meal", source_id=meal_id_str,
                metadata={"meal": meal_label}, occurred_at=date,
            )
    return food_log_id


async def insert_exploded_meal_items_log(user_id: int, date: str, meal_label: str, items) -> list:
    pool = await get_pool()
    food_log_ids = []
    async with pool.acquire() as conn:
        async with conn.transaction():
            for item in items:
                food_log_id = await insert_returning(conn, "food_log", {
                    "user_id": user_id, "date": date, "meal": meal_label, "food_name": item["food_name"],
                    "source": item["source"], "source_id": item["source_id"], "category": item["category"],
                    "serving_size": item["serving_size"], "serving_unit": item["serving_unit"],
                    "calories": item["calories"], "nutrients_json": json.dumps(item["nutrients"]),
                })
                food_log_ids.append(food_log_id)
                await write_nutrients(conn, "food_log", food_log_id, item["nutrients"])
                await log_domain_event(
                    conn, user_id, "food_log", food_log_id, "created",
                    category=item["category"], amount=item["calories"], label=item["food_name"],
                    source=item["source"], source_id=item["source_id"], metadata={"meal": meal_label},
                    occurred_at=date,
                )
    return food_log_ids


async def get_combined_meal_log_entry(food_log_id: int, user_id: int, meal_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT * FROM food_log WHERE id = $1 AND user_id = $2 AND source = 'meal' AND source_id = $3",
            food_log_id, user_id, str(meal_id),
        )


async def replace_combined_entry_with_items(food_log_id: int, user_id: int, date: str, meal_label: str, items) -> list:
    # NOTE: the DELETE below intentionally has no user_id check, matching
    # the original -- ownership is already confirmed by the caller's
    # earlier get_combined_meal_log_entry() lookup before this runs.
    """Deletes the one combined food_log entry (+ its nutrient_facts) and
    inserts one food_log entry per meal item, all in one transaction."""
    pool = await get_pool()
    food_log_ids = []
    async with pool.acquire() as conn:
        async with conn.transaction():
            await delete_nutrient_facts(conn, "food_log", food_log_id)
            deleted = await conn.fetchrow(
                "DELETE FROM food_log WHERE id = $1 RETURNING date, food_name, source, source_id, category, calories",
                food_log_id,
            )
            if deleted:
                await log_domain_event(
                    conn, user_id, "food_log", food_log_id, "deleted",
                    category=deleted["category"], amount=deleted["calories"], label=deleted["food_name"],
                    source=deleted["source"], source_id=deleted["source_id"], occurred_at=deleted["date"],
                    metadata={"reason": "exploded_into_items"},
                )
            for item in items:
                new_id = await insert_returning(conn, "food_log", {
                    "user_id": user_id, "date": date, "meal": meal_label, "food_name": item["food_name"],
                    "source": item["source"], "source_id": item["source_id"], "category": item["category"],
                    "serving_size": item["serving_size"], "serving_unit": item["serving_unit"],
                    "calories": item["calories"], "nutrients_json": json.dumps(item["nutrients"]),
                })
                food_log_ids.append(new_id)
                await write_nutrients(conn, "food_log", new_id, item["nutrients"])
                await log_domain_event(
                    conn, user_id, "food_log", new_id, "created",
                    category=item["category"], amount=item["calories"], label=item["food_name"],
                    source=item["source"], source_id=item["source_id"], metadata={"meal": meal_label},
                    occurred_at=date,
                )
    return food_log_ids

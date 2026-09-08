import json

from .. import get_pool
from ...nutrient_facts import (
    write_nutrients, read_nutrients_bulk, delete_nutrient_facts, delete_nutrient_facts_bulk,
)

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
            meal_id = await conn.fetchval(
                "INSERT INTO meals (user_id, name, category, category_is_custom) VALUES ($1, $2, $3, $4) RETURNING id",
                user_id, name, resolved_category, category_is_custom,
            )
            await _save_items(conn, meal_id, items)
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


async def update_meal(meal_id: int, name: str, resolved_category, category_is_custom, items) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE meals SET name = $1, category = $2, category_is_custom = $3, updated_at = now() WHERE id = $4",
                name, resolved_category, category_is_custom, meal_id,
            )
            old_item_ids = [r["id"] for r in await conn.fetch(
                "SELECT id FROM meal_items WHERE meal_id = $1", meal_id
            )]
            await delete_nutrient_facts_bulk(conn, "meal_item", old_item_ids)
            await conn.execute("DELETE FROM meal_items WHERE meal_id = $1", meal_id)
            await _save_items(conn, meal_id, items)


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
            await conn.execute("DELETE FROM meals WHERE id = $1 AND user_id = $2", meal_id, user_id)


async def insert_combined_meal_log(
    user_id: int, date: str, meal_label: str, meal_name: str, meal_id_str: str, category, calories: float, nutrients: dict,
) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            food_log_id = await conn.fetchval(
                """INSERT INTO food_log (user_id, date, meal, food_name, source, source_id,
                       category, serving_size, serving_unit, calories, nutrients_json)
                   VALUES ($1, $2, $3, $4, 'meal', $5, $6, 1, 'meal', $7, $8)
                   RETURNING id""",
                user_id, date, meal_label, meal_name, meal_id_str, category,
                calories, json.dumps(nutrients),
            )
            await write_nutrients(conn, "food_log", food_log_id, nutrients)
    return food_log_id


async def insert_exploded_meal_items_log(user_id: int, date: str, meal_label: str, items) -> list:
    pool = await get_pool()
    food_log_ids = []
    async with pool.acquire() as conn:
        async with conn.transaction():
            for item in items:
                food_log_id = await conn.fetchval(
                    """INSERT INTO food_log (user_id, date, meal, food_name, source, source_id,
                           category, serving_size, serving_unit, calories, nutrients_json)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                       RETURNING id""",
                    user_id, date, meal_label, item["food_name"], item["source"], item["source_id"],
                    item["category"], item["serving_size"], item["serving_unit"], item["calories"], json.dumps(item["nutrients"]),
                )
                food_log_ids.append(food_log_id)
                await write_nutrients(conn, "food_log", food_log_id, item["nutrients"])
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
            await conn.execute("DELETE FROM food_log WHERE id = $1", food_log_id)
            for item in items:
                new_id = await conn.fetchval(
                    """INSERT INTO food_log (user_id, date, meal, food_name, source, source_id,
                           category, serving_size, serving_unit, calories, nutrients_json)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                       RETURNING id""",
                    user_id, date, meal_label, item["food_name"], item["source"], item["source_id"],
                    item["category"], item["serving_size"], item["serving_unit"], item["calories"], json.dumps(item["nutrients"]),
                )
                food_log_ids.append(new_id)
                await write_nutrients(conn, "food_log", new_id, item["nutrients"])
    return food_log_ids

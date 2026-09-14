from .. import get_pool, delete_with_ownership_returning
from ..sql_builder import select_clause, where_clause, order_by_clause
from ...nutrient_facts import read_nutrients_bulk, delete_nutrient_facts
from ...domain_events import log_domain_event


async def search_recipes_by_name(user_id: int, query: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        sql = (
            select_clause("recipes", ["id", "name", "servings_per_batch"])
            + where_clause(["user_id = $1", "name ILIKE $2"])
            + order_by_clause("name")
        )
        return await conn.fetch(sql, user_id, f"%{query}%")


async def search_meals_by_name(user_id: int, query: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        sql = (
            select_clause("meals", ["id", "name"])
            + where_clause(["user_id = $1", "name ILIKE $2"])
            + order_by_clause("name")
        )
        return await conn.fetch(sql, user_id, f"%{query}%")


async def search_pantry_by_name(user_id: int, query: str):
    """Returns (rows, nutrients_by_item) -- kept together since both are
    read within the same acquired connection, same pattern as
    list_food_log above. Excludes finished items, matching
    list_pantry_items' own 'gone means gone' filter -- searching to log a
    food you already used up isn't useful."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        sql = (
            select_clause("pantry_items", ["id", "food_name", "category", "serving_size", "serving_unit", "calories"])
            + where_clause(["user_id = $1", "is_finished = FALSE", "food_name ILIKE $2"])
            + order_by_clause("food_name")
        )
        rows = await conn.fetch(sql, user_id, f"%{query}%")
        item_ids = [r["id"] for r in rows]
        nutrients_by_item = await read_nutrients_bulk(conn, "pantry_item", item_ids)
    return rows, nutrients_by_item


async def list_food_log(user_id: int, date: str):
    """Returns (rows, nutrients_by_entry) -- kept together since the
    original call read both within the same acquired connection."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        sql = select_clause("food_log") + where_clause(["user_id = $1", "date = $2"]) + order_by_clause("id")
        rows = await conn.fetch(sql, user_id, date)
        entry_ids = [r["id"] for r in rows]
        nutrients_by_entry = await read_nutrients_bulk(conn, "food_log", entry_ids)
    return rows, nutrients_by_entry


async def delete_food_entry(entry_id: int, user_id: int) -> None:
    """nutrient_facts has no FK to cascade automatically (see
    app/nutrient_facts.py), so its rows for this entry are deleted
    explicitly first, in the same transaction as the food_log delete.
    delete_with_ownership_returning captures the row's own data for the
    domain event in the same round trip, rather than a separate SELECT
    before it -- if entry_id didn't belong to user_id, it yields nothing
    and no event is logged, matching the delete itself being a no-op."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await delete_nutrient_facts(conn, "food_log", entry_id)
            deleted = await delete_with_ownership_returning(
                conn, "food_log", entry_id, user_id,
                ["date", "food_name", "category", "calories", "source", "source_id"],
            )
            if deleted:
                await log_domain_event(
                    conn, user_id, "food_log", entry_id, "deleted",
                    category=deleted["category"], amount=deleted["calories"], label=deleted["food_name"],
                    source=deleted["source"], source_id=deleted["source_id"], occurred_at=deleted["date"],
                )

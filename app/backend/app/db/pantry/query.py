import json

from .. import get_pool
from ...nutrient_facts import write_nutrients, delete_nutrient_facts
from ...portion_scaling import scale_macros, scale_nutrients, multiple_based_factor


async def create_pantry_item(
    user_id: int, food_name, source, source_id, category, serving_size, serving_unit,
    tracking_mode, remaining_servings, expiration_date, calories, nutrients: dict,
) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            item_id = await conn.fetchval(
                """INSERT INTO pantry_items (user_id, food_name, source, source_id, category, serving_size,
                       serving_unit, tracking_mode, remaining_servings, expiration_date,
                       calories, nutrients_json)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                   RETURNING id""",
                user_id, food_name, source, source_id, category, serving_size,
                serving_unit, tracking_mode, remaining_servings, expiration_date,
                calories, json.dumps(nutrients),
            )
            await write_nutrients(conn, "pantry_item", item_id, nutrients)
    return item_id


async def list_pantry_items(user_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT * FROM pantry_items WHERE user_id = $1 AND is_finished = FALSE ORDER BY expiration_date NULLS LAST, added_at",
            user_id,
        )


async def list_expiring_items(user_id: int, cutoff: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(
            """SELECT * FROM pantry_items
               WHERE user_id = $1 AND is_finished = FALSE
                 AND expiration_date IS NOT NULL
                 AND expiration_date <= $2
               ORDER BY expiration_date""",
            user_id, cutoff,
        )


async def get_pantry_item_for_update(item_id: int, user_id: int):
    """Separate from the actual update below (was one connection in the
    original) so the router can validate tracking_mode -- and raise 404
    vs 400 in the original order -- between the existence check and the
    write; see commit message."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT * FROM pantry_items WHERE id = $1 AND user_id = $2", item_id, user_id
        )


async def update_pantry_item(item_id: int, user_id: int, remaining_servings, expiration_date, tracking_mode) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE pantry_items SET
                   remaining_servings = COALESCE($1, remaining_servings),
                   expiration_date = COALESCE($2, expiration_date),
                   tracking_mode = COALESCE($3, tracking_mode),
                   updated_at = now()
               WHERE id = $4 AND user_id = $5""",
            remaining_servings, expiration_date, tracking_mode, item_id, user_id,
        )


async def delete_pantry_item(item_id: int, user_id: int) -> None:
    """Deletes pantry_items FIRST (its WHERE clause is the ownership
    check), then only cleans up nutrient_facts if that actually removed a
    row -- nutrient_facts has no user_id column of its own, so calling
    delete_nutrient_facts for an item_id belonging to a different user
    (or not existing) would wipe that other user's row instead of a
    no-op."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            result = await conn.execute(
                "DELETE FROM pantry_items WHERE id = $1 AND user_id = $2", item_id, user_id
            )
            if result != "DELETE 0":
                await delete_nutrient_facts(conn, "pantry_item", item_id)


async def finish_pantry_item(item_id: int, user_id: int) -> bool:
    """Returns True if a row was deleted, False if no matching row was
    found. Same delete-then-conditionally-clean-up ordering as
    delete_pantry_item, for the same reason."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            result = await conn.execute(
                "DELETE FROM pantry_items WHERE id = $1 AND user_id = $2", item_id, user_id
            )
            if result != "DELETE 0":
                await delete_nutrient_facts(conn, "pantry_item", item_id)
    return result != "DELETE 0"


async def consume_pantry_item(item_id: int, user_id: int, servings: float, date: str, meal: str) -> dict:
    """Atomic pantry-to-diary action, entirely within one FOR UPDATE
    transaction -- the scaling computation (portion_scaling helpers)
    stays inside this one function rather than being split out to the
    router, because splitting it would mean re-fetching the row outside
    the row lock, defeating the lock's whole purpose (preventing a
    concurrent double-consume race between the read and the write).

    Returns one of:
      {"error": "not_found"}
      {"error": "insufficient", "remaining": <float or None>}
      {"food_log_id": <int>, "pantry_status": "unchanged"|"removed"|"decremented"}
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            item = await conn.fetchrow(
                "SELECT * FROM pantry_items WHERE id = $1 AND user_id = $2 FOR UPDATE",
                item_id, user_id,
            )
            if item is None:
                return {"error": "not_found"}

            if item["tracking_mode"] == "countable":
                if item["remaining_servings"] is None or servings > item["remaining_servings"]:
                    return {"error": "insufficient", "remaining": item["remaining_servings"]}

            factor = multiple_based_factor(servings)
            macros = scale_macros({"calories": item["calories"]}, factor)
            stored_nutrients = json.loads(item["nutrients_json"]) if item["nutrients_json"] else {}
            nutrients = scale_nutrients(stored_nutrients, factor)

            food_log_id = await conn.fetchval(
                """INSERT INTO food_log (user_id, date, meal, food_name, source, source_id,
                       category, serving_size, serving_unit, calories, nutrients_json)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                   RETURNING id""",
                user_id, date, meal, item["food_name"], item["source"], item["source_id"],
                item["category"], servings, item["serving_unit"], macros["calories"], json.dumps(nutrients),
            )
            await write_nutrients(conn, "food_log", food_log_id, nutrients)

            pantry_status = "unchanged"
            if item["tracking_mode"] == "single":
                await delete_nutrient_facts(conn, "pantry_item", item_id)
                await conn.execute("DELETE FROM pantry_items WHERE id = $1", item_id)
                pantry_status = "removed"
            elif item["tracking_mode"] == "countable":
                new_remaining = item["remaining_servings"] - servings
                if new_remaining <= 0:
                    await delete_nutrient_facts(conn, "pantry_item", item_id)
                    await conn.execute("DELETE FROM pantry_items WHERE id = $1", item_id)
                    pantry_status = "removed"
                else:
                    await conn.execute(
                        "UPDATE pantry_items SET remaining_servings = $1, updated_at = now() WHERE id = $2",
                        new_remaining, item_id,
                    )
                    pantry_status = "decremented"

    return {"food_log_id": food_log_id, "pantry_status": pantry_status}


async def remove_pantry_servings(item_id: int, user_id: int, servings: float) -> dict:
    """Mirrors consume_pantry_item's decrement/delete-at-zero logic
    exactly, just skips the food_log insert -- same FOR UPDATE-must-stay-
    atomic reasoning applies. Returns one of:
      {"error": "not_found"}
      {"error": "not_countable"}
      {"error": "insufficient", "remaining": <float or None>}
      {"pantry_status": "removed"|"decremented"}
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            item = await conn.fetchrow(
                "SELECT * FROM pantry_items WHERE id = $1 AND user_id = $2 FOR UPDATE",
                item_id, user_id,
            )
            if item is None:
                return {"error": "not_found"}
            if item["tracking_mode"] != "countable":
                return {"error": "not_countable"}
            if item["remaining_servings"] is None or servings > item["remaining_servings"]:
                return {"error": "insufficient", "remaining": item["remaining_servings"]}

            new_remaining = item["remaining_servings"] - servings
            if new_remaining <= 0:
                await delete_nutrient_facts(conn, "pantry_item", item_id)
                await conn.execute("DELETE FROM pantry_items WHERE id = $1", item_id)
                pantry_status = "removed"
            else:
                await conn.execute(
                    "UPDATE pantry_items SET remaining_servings = $1, updated_at = now() WHERE id = $2",
                    new_remaining, item_id,
                )
                pantry_status = "decremented"

    return {"pantry_status": pantry_status}

import json

from .. import get_pool, delete_with_ownership_returning, insert_returning, update_with_ownership_returning
from ..sql_builder import select_clause, where_clause, order_by_clause, update_clause, set_clause, delete_clause
from ...nutrient_facts import write_nutrients, delete_nutrient_facts
from ...portion_scaling import scale_macros, scale_nutrients, multiple_based_factor
from ...domain_events import log_domain_event


async def create_pantry_item(
    user_id: int, food_name, source, source_id, category, serving_size, serving_unit,
    tracking_mode, remaining_servings, expiration_date, calories, nutrients: dict,
) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            item_id = await insert_returning(conn, "pantry_items", {
                "user_id": user_id, "food_name": food_name, "source": source, "source_id": source_id,
                "category": category, "serving_size": serving_size, "serving_unit": serving_unit,
                "tracking_mode": tracking_mode, "remaining_servings": remaining_servings,
                "expiration_date": expiration_date, "calories": calories, "nutrients_json": json.dumps(nutrients),
            })
            await write_nutrients(conn, "pantry_item", item_id, nutrients)
            await log_domain_event(
                conn, user_id, "pantry_item", item_id, "created",
                category=category.value if category else None, amount=calories, label=food_name,
                source=source, source_id=source_id,
                metadata={"tracking_mode": tracking_mode, "remaining_servings": remaining_servings},
            )
    return item_id


async def list_pantry_items(user_id: int):
    """expiration_date NULLS LAST isn't expressible through
    order_by_clause's plain "column [ASC|DESC]" shape (NULLS LAST is a
    third, non-direction modifier) -- passed through as-is here since
    it's a fixed literal, not a request-derived value."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        sql = (
            select_clause("pantry_items")
            + where_clause(["user_id = $1", "is_finished = FALSE"])
            + " ORDER BY expiration_date NULLS LAST, added_at"
        )
        return await conn.fetch(sql, user_id)


async def list_expiring_items(user_id: int, cutoff: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        sql = (
            select_clause("pantry_items")
            + where_clause([
                "user_id = $1", "is_finished = FALSE",
                "expiration_date IS NOT NULL", "expiration_date <= $2",
            ])
            + order_by_clause("expiration_date")
        )
        return await conn.fetch(sql, user_id, cutoff)


async def get_pantry_item_for_update(item_id: int, user_id: int):
    """Separate from the actual update below (was one connection in the
    original) so the router can validate tracking_mode -- and raise 404
    vs 400 in the original order -- between the existence check and the
    write; see commit message."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        sql = select_clause("pantry_items") + where_clause(["id = $1", "user_id = $2"])
        return await conn.fetchrow(sql, item_id, user_id)


async def update_pantry_item(item_id: int, user_id: int, remaining_servings, expiration_date, tracking_mode) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await update_with_ownership_returning(
                conn, "pantry_items", item_id, user_id,
                {"remaining_servings": remaining_servings, "expiration_date": expiration_date, "tracking_mode": tracking_mode},
                ["food_name", "category", "calories", "source", "source_id"],
            )
            if row:
                await log_domain_event(
                    conn, user_id, "pantry_item", item_id, "updated",
                    category=row["category"], amount=row["calories"], label=row["food_name"],
                    source=row["source"], source_id=row["source_id"],
                )


async def delete_pantry_item(item_id: int, user_id: int) -> None:
    """Deletes pantry_items FIRST (its WHERE clause is the ownership
    check), then only cleans up nutrient_facts if that actually removed a
    row -- nutrient_facts has no user_id column of its own, so calling
    delete_nutrient_facts for an item_id belonging to a different user
    (or not existing) would wipe that other user's row instead of a
    no-op. delete_with_ownership_returning doubles as that same "did this
    actually delete something" signal for the domain event."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            deleted = await delete_with_ownership_returning(
                conn, "pantry_items", item_id, user_id, ["food_name", "category", "calories", "source", "source_id"],
            )
            if deleted:
                await delete_nutrient_facts(conn, "pantry_item", item_id)
                await log_domain_event(
                    conn, user_id, "pantry_item", item_id, "deleted",
                    category=deleted["category"], amount=deleted["calories"], label=deleted["food_name"],
                    source=deleted["source"], source_id=deleted["source_id"],
                )


async def finish_pantry_item(item_id: int, user_id: int) -> bool:
    """Returns True if a row was deleted, False if no matching row was
    found. Same delete-then-conditionally-clean-up ordering as
    delete_pantry_item, for the same reason."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            deleted = await delete_with_ownership_returning(
                conn, "pantry_items", item_id, user_id, ["food_name", "category", "calories", "source", "source_id"],
            )
            if deleted:
                await delete_nutrient_facts(conn, "pantry_item", item_id)
                await log_domain_event(
                    conn, user_id, "pantry_item", item_id, "deleted",
                    category=deleted["category"], amount=deleted["calories"], label=deleted["food_name"],
                    source=deleted["source"], source_id=deleted["source_id"],
                    metadata={"reason": "finished"},
                )
    return deleted is not None


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
                select_clause("pantry_items") + where_clause(["id = $1", "user_id = $2"]) + " FOR UPDATE",
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

            food_log_id = await insert_returning(conn, "food_log", {
                "user_id": user_id, "date": date, "meal": meal, "food_name": item["food_name"],
                "source": item["source"], "source_id": item["source_id"], "category": item["category"],
                "serving_size": servings, "serving_unit": item["serving_unit"],
                "calories": macros["calories"], "nutrients_json": json.dumps(nutrients),
            })
            await write_nutrients(conn, "food_log", food_log_id, nutrients)
            await log_domain_event(
                conn, user_id, "food_log", food_log_id, "created",
                category=item["category"], amount=macros["calories"], label=item["food_name"],
                source=item["source"], source_id=item["source_id"],
                metadata={"meal": meal, "consumed_from_pantry_item": item_id},
                # The entry's own date, not "now": Goals/Targets bucket
                # consumption by occurred_at, so omitting this filed a
                # backdated (or future-dated) pantry consume under the day it
                # was clicked -- found 2026-09-24 when the dashboard's
                # progress moved onto the event log.
                occurred_at=date,
            )

            pantry_status = "unchanged"
            if item["tracking_mode"] == "single":
                await delete_nutrient_facts(conn, "pantry_item", item_id)
                await conn.execute(delete_clause("pantry_items") + where_clause(["id = $1"]), item_id)
                pantry_status = "removed"
                await log_domain_event(
                    conn, user_id, "pantry_item", item_id, "deleted",
                    category=item["category"], amount=item["calories"], label=item["food_name"],
                    source=item["source"], source_id=item["source_id"], metadata={"reason": "consumed"},
                )
            elif item["tracking_mode"] == "countable":
                new_remaining = item["remaining_servings"] - servings
                if new_remaining <= 0:
                    await delete_nutrient_facts(conn, "pantry_item", item_id)
                    await conn.execute(delete_clause("pantry_items") + where_clause(["id = $1"]), item_id)
                    pantry_status = "removed"
                    await log_domain_event(
                        conn, user_id, "pantry_item", item_id, "deleted",
                        category=item["category"], amount=item["calories"], label=item["food_name"],
                        source=item["source"], source_id=item["source_id"], metadata={"reason": "consumed"},
                    )
                else:
                    await conn.execute(
                        update_clause("pantry_items")
                        + set_clause(["remaining_servings = $1", "updated_at = now()"])
                        + where_clause(["id = $2"]),
                        new_remaining, item_id,
                    )
                    pantry_status = "decremented"
                    await log_domain_event(
                        conn, user_id, "pantry_item", item_id, "updated",
                        category=item["category"], amount=item["calories"], label=item["food_name"],
                        source=item["source"], source_id=item["source_id"],
                        metadata={"reason": "consumed", "remaining_servings": new_remaining},
                    )

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
                select_clause("pantry_items") + where_clause(["id = $1", "user_id = $2"]) + " FOR UPDATE",
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
                await conn.execute(delete_clause("pantry_items") + where_clause(["id = $1"]), item_id)
                pantry_status = "removed"
                await log_domain_event(
                    conn, user_id, "pantry_item", item_id, "deleted",
                    category=item["category"], amount=item["calories"], label=item["food_name"],
                    source=item["source"], source_id=item["source_id"], metadata={"reason": "removed_no_log"},
                )
            else:
                await conn.execute(
                    update_clause("pantry_items")
                    + set_clause(["remaining_servings = $1", "updated_at = now()"])
                    + where_clause(["id = $2"]),
                    new_remaining, item_id,
                )
                pantry_status = "decremented"
                await log_domain_event(
                    conn, user_id, "pantry_item", item_id, "updated",
                    category=item["category"], amount=item["calories"], label=item["food_name"],
                    source=item["source"], source_id=item["source_id"],
                    metadata={"reason": "removed_no_log", "remaining_servings": new_remaining},
                )

    return {"pantry_status": pantry_status}

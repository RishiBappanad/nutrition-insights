import json

from .. import get_pool
from ...nutrient_facts import (
    write_nutrients, read_nutrients_bulk, delete_nutrient_facts, delete_nutrient_facts_bulk,
)

# resolve_category(), _aggregate_batch_totals() (pure computation, stays in
# routers/recipes.py -- food.py imports it from there too, unchanged), and
# food_entry_contract.import_recipe() (create_recipe's own write path)
# deliberately stay out of this module -- same "business logic stays at
# its existing layer" treatment as every other file in this pass.


async def _save_items(conn, recipe_id: int, items) -> None:
    for item in items:
        item_id = await conn.fetchval(
            """INSERT INTO recipe_items (recipe_id, food_name, source, source_id, category, amount_grams, amount_multiple,
                   calories, nutrients_json)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
               RETURNING id""",
            recipe_id, item.food_name, item.source, item.source_id, item.category,
            item.amount_grams, item.amount_multiple, item.calories, json.dumps(item.nutrients),
        )
        await write_nutrients(conn, "recipe_item", item_id, item.nutrients)


async def list_recipes(user_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT id, name, servings_per_batch, category, created_at, updated_at FROM recipes WHERE user_id = $1 ORDER BY name",
            user_id,
        )


async def get_recipe_with_items(conn, recipe_id: int, user_id: int):
    recipe = await conn.fetchrow("SELECT * FROM recipes WHERE id = $1 AND user_id = $2", recipe_id, user_id)
    if recipe is None:
        return None, []
    item_rows = await conn.fetch("SELECT * FROM recipe_items WHERE recipe_id = $1 ORDER BY id", recipe_id)
    item_ids = [r["id"] for r in item_rows]
    nutrients_by_item = await read_nutrients_bulk(conn, "recipe_item", item_ids)
    items = []
    for r in item_rows:
        items.append({
            "id": r["id"],
            "food_name": r["food_name"],
            "source": r["source"],
            "source_id": r["source_id"],
            "category": r["category"],
            "amount_grams": r["amount_grams"],
            "amount_multiple": r["amount_multiple"],
            "calories": r["calories"],
            "nutrients": nutrients_by_item.get(r["id"], {}),
        })
    return recipe, items


async def get_recipe_with_items_new_conn(recipe_id: int, user_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await get_recipe_with_items(conn, recipe_id, user_id)


async def get_recipe_for_update(recipe_id: int, user_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT id, category, category_is_custom FROM recipes WHERE id = $1 AND user_id = $2", recipe_id, user_id
        )


async def update_recipe(recipe_id: int, name: str, servings_per_batch: float, resolved_category, category_is_custom, items) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """UPDATE recipes SET name = $1, servings_per_batch = $2,
                       category = $3, category_is_custom = $4, updated_at = now()
                   WHERE id = $5""",
                name, servings_per_batch, resolved_category, category_is_custom, recipe_id,
            )
            old_item_ids = [r["id"] for r in await conn.fetch(
                "SELECT id FROM recipe_items WHERE recipe_id = $1", recipe_id
            )]
            await delete_nutrient_facts_bulk(conn, "recipe_item", old_item_ids)
            await conn.execute("DELETE FROM recipe_items WHERE recipe_id = $1", recipe_id)
            await _save_items(conn, recipe_id, items)


async def delete_recipe(recipe_id: int, user_id: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            item_ids = [r["id"] for r in await conn.fetch(
                """SELECT ri.id FROM recipe_items ri
                   JOIN recipes r ON r.id = ri.recipe_id
                   WHERE ri.recipe_id = $1 AND r.user_id = $2""",
                recipe_id, user_id,
            )]
            await delete_nutrient_facts_bulk(conn, "recipe_item", item_ids)
            await conn.execute("DELETE FROM recipes WHERE id = $1 AND user_id = $2", recipe_id, user_id)


async def insert_recipe_log(
    user_id: int, date: str, meal: str, recipe_name: str, recipe_id_str: str, category,
    servings: float, calories: float, nutrients: dict,
) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            food_log_id = await conn.fetchval(
                """INSERT INTO food_log (user_id, date, meal, food_name, source, source_id,
                       category, serving_size, serving_unit, calories, nutrients_json)
                   VALUES ($1, $2, $3, $4, 'recipe', $5, $6, $7, 'serving', $8, $9)
                   RETURNING id""",
                user_id, date, meal, recipe_name, recipe_id_str, category, servings,
                calories, json.dumps(nutrients),
            )
            await write_nutrients(conn, "food_log", food_log_id, nutrients)
    return food_log_id


async def _match_recipe_against_pantry(conn, items, user_id: int) -> dict:
    """Shared matching logic for can_make_recipe AND make_recipe below --
    isolated here (moved verbatim from routers/recipes.py) so the two can
    never silently disagree about what counts as available. Kept as a
    conn-taking internal helper, not split into pure-SQL-vs-business-logic
    pieces, since make_recipe calls it from inside its own already-open
    FOR UPDATE transaction -- see make_recipe's docstring for why that
    whole flow stays together."""
    pantry_rows = await conn.fetch(
        "SELECT * FROM pantry_items WHERE user_id = $1 AND is_finished = FALSE", user_id
    )
    pantry_by_source = {(p["source"], p["source_id"]): p for p in pantry_rows if p["source"] and p["source_id"]}

    have, missing, unmatchable = [], [], []
    for item in items:
        key = (item["source"], item["source_id"])
        if not item["source"] or not item["source_id"]:
            unmatchable.append({"food_name": item["food_name"]})
            continue

        pantry_item = pantry_by_source.get(key)
        if pantry_item is None:
            missing.append({"food_name": item["food_name"]})
            continue

        requested = item.get("amount_multiple")
        entry = {
            "food_name": item["food_name"], "pantry_item_id": pantry_item["id"],
            "tracking_mode": pantry_item["tracking_mode"], "requested_servings": requested,
        }
        if pantry_item["tracking_mode"] == "countable" and requested is not None:
            if pantry_item["remaining_servings"] is not None and requested > pantry_item["remaining_servings"]:
                entry["sufficient"] = False
                entry["remaining_servings"] = pantry_item["remaining_servings"]
                missing.append(entry)
                continue
        have.append(entry)

    return {"have": have, "missing": missing, "unmatchable": unmatchable}


async def can_make_recipe(recipe_id: int, user_id: int):
    """Returns (recipe, match_dict), or (None, None) if the recipe wasn't
    found."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        recipe, items = await get_recipe_with_items(conn, recipe_id, user_id)
        if recipe is None:
            return None, None
        match = await _match_recipe_against_pantry(conn, items, user_id)
    return recipe, match


async def make_recipe(recipe_id: int, user_id: int) -> dict:
    """Entirely within one transaction, matching the original -- re-runs
    the can-make matching, then decrements/removes matched pantry items
    (using SELECT ... FOR UPDATE per item, same as pantry.py's
    consume_pantry_item), then adds one new pantry item for the finished
    batch. Kept as a single self-contained function for the same reason
    as pantry.py's FOR UPDATE functions: splitting the match-then-lock
    sequence across separate connections would let the pantry state
    change between the check and the lock in ways the code isn't
    designed to reconcile (it already tolerates a matched row vanishing
    between match and lock -- "already gone" -- but not the reverse).

    Returns one of:
      {"error": "not_found"}
      {"error": "missing_ingredients", **match}
      {success payload}
    """
    from ...routers.food import _recipe_per_serving_nutrition

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            recipe, items = await get_recipe_with_items(conn, recipe_id, user_id)
            if recipe is None:
                return {"error": "not_found"}

            match = await _match_recipe_against_pantry(conn, items, user_id)
            if match["missing"] or match["unmatchable"]:
                return {"error": "missing_ingredients", **match}

            decremented, removed = [], []
            for entry in match["have"]:
                pantry_item = await conn.fetchrow(
                    "SELECT * FROM pantry_items WHERE id = $1 AND user_id = $2 FOR UPDATE",
                    entry["pantry_item_id"], user_id,
                )
                if pantry_item is None:
                    continue  # already gone (e.g. duplicate recipe items referencing the same pantry row)

                if pantry_item["tracking_mode"] == "single":
                    await delete_nutrient_facts(conn, "pantry_item", pantry_item["id"])
                    await conn.execute("DELETE FROM pantry_items WHERE id = $1", pantry_item["id"])
                    removed.append(pantry_item["id"])
                elif pantry_item["tracking_mode"] == "countable" and entry["requested_servings"] is not None:
                    new_remaining = pantry_item["remaining_servings"] - entry["requested_servings"]
                    if new_remaining <= 0:
                        await delete_nutrient_facts(conn, "pantry_item", pantry_item["id"])
                        await conn.execute("DELETE FROM pantry_items WHERE id = $1", pantry_item["id"])
                        removed.append(pantry_item["id"])
                    else:
                        await conn.execute(
                            "UPDATE pantry_items SET remaining_servings = $1, updated_at = now() WHERE id = $2",
                            new_remaining, pantry_item["id"],
                        )
                        decremented.append(pantry_item["id"])
                # bulk: presence-only, never decremented -- matches
                # can-make's own bulk handling (no quantity concept).

            per_serving_macros, per_serving_nutrients = _recipe_per_serving_nutrition(recipe, items)
            pantry_item_id = await conn.fetchval(
                """INSERT INTO pantry_items (user_id, food_name, source, source_id, category, serving_size,
                       serving_unit, tracking_mode, remaining_servings,
                       calories, nutrients_json)
                   VALUES ($1, $2, 'recipe', $3, $4, 1, 'serving', 'countable', $5, $6, $7)
                   RETURNING id""",
                user_id, recipe["name"], str(recipe_id), recipe["category"], recipe["servings_per_batch"],
                per_serving_macros["calories"], json.dumps(per_serving_nutrients),
            )
            await write_nutrients(conn, "pantry_item", pantry_item_id, per_serving_nutrients)

    return {
        "pantry_item_id": pantry_item_id,
        "servings_added": recipe["servings_per_batch"],
        "ingredients_decremented": decremented,
        "ingredients_removed": removed,
    }

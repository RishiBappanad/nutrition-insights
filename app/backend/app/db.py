import os
from typing import Optional
import asyncpg
from cryptography.fernet import Fernet
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
FERNET_KEY = os.getenv("FERNET_KEY", Fernet.generate_key().decode())

_fernet = Fernet(FERNET_KEY.encode() if isinstance(FERNET_KEY, str) else FERNET_KEY)

_pool: Optional[asyncpg.Pool] = None


def encrypt(value: str) -> str:
    return _fernet.encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    return _fernet.decrypt(value.encode()).decode()


async def get_pool() -> asyncpg.Pool:
    """Get (or lazily create) the shared connection pool.

    statement_cache_size=0 is required because DATABASE_URL points at
    Neon's PgBouncer-pooled endpoint (transaction pooling mode) — asyncpg
    normally caches prepared statement plans per-connection, but under
    transaction pooling a given asyncpg "connection" can be multiplexed
    across different real Postgres backend connections request-to-request,
    so a cached plan can silently point at a backend where the schema has
    since changed underneath it. This surfaced as a real production 500
    (asyncpg.exceptions.InvalidCachedStatementError) immediately after a
    live ALTER TABLE — confirmed via Cloud Run logs, not a hypothetical.
    Disabling the statement cache is the standard, documented fix for
    asyncpg + PgBouncer transaction pooling (see MagicStack/asyncpg#507,
    #1065) and costs one extra prepare-and-execute round trip per query
    instead of a cached plan — an acceptable tradeoff for correctness
    over the alternative of every future schema change risking another
    production outage until the pool happens to cycle."""
    global _pool
    if _pool is None:
        if not DATABASE_URL:
            raise RuntimeError("DATABASE_URL must be set")
        _pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10, statement_cache_size=0)
    return _pool


async def init_db():
    """Create app-level tables (users, credentials) plus user-scoped data
    tables (daily_nutrition, lift_orm, food_log) if they don't already exist.
    All data tables are scoped by user_id since Postgres is shared across users.

    `users.id` is NOT auto-generated here — it's the account_id assigned by
    trackstack-auth, the shared identity service. This table is a local
    mirror (created lazily on first authenticated request, see
    routers/auth.py::_ensure_local_user), not the source of truth for
    identity/passwords."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL DEFAULT 'trackstack-auth',
                created_at TIMESTAMPTZ DEFAULT now()
            );

            CREATE TABLE IF NOT EXISTS credentials (
                user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
                hevy_username TEXT,
                hevy_password TEXT,
                cronometer_username TEXT,
                cronometer_password TEXT
            );

            CREATE TABLE IF NOT EXISTS daily_nutrition (
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
                date TEXT NOT NULL,
                metric TEXT NOT NULL,
                value DOUBLE PRECISION NOT NULL,
                PRIMARY KEY (user_id, date, metric)
            );

            CREATE TABLE IF NOT EXISTS lift_orm (
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
                date TEXT NOT NULL,
                exercise TEXT NOT NULL,
                orm DOUBLE PRECISION NOT NULL,
                PRIMARY KEY (user_id, date, exercise)
            );

            CREATE TABLE IF NOT EXISTS food_log (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
                date TEXT NOT NULL,
                meal TEXT DEFAULT 'Snack',
                food_name TEXT NOT NULL,
                source TEXT,
                source_id TEXT,
                serving_size DOUBLE PRECISION DEFAULT 1.0,
                serving_unit TEXT DEFAULT 'serving',
                calories DOUBLE PRECISION DEFAULT 0,
                protein DOUBLE PRECISION DEFAULT 0,
                carbs DOUBLE PRECISION DEFAULT 0,
                fat DOUBLE PRECISION DEFAULT 0,
                fiber DOUBLE PRECISION DEFAULT 0,
                nutrients_json TEXT
            );

            -- Additive column for the TrackStack -> Cronometer push sync
            -- pointer (cronometer_sync_state.last_pushed_at) to find
            -- entries created since the last push -- food_log predates
            -- this feature and had no creation timestamp at all.
            ALTER TABLE food_log ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now();

            -- Normalized per-nutrient breakdown for a food_log entry.
            -- Replaces relying on nutrients_json (write-only, never read back
            -- structured) so per-nutrient daily totals can be aggregated with
            -- SQL instead of parsing JSON in application code for every row.
            CREATE TABLE IF NOT EXISTS food_log_nutrients (
                food_log_id INTEGER NOT NULL REFERENCES food_log(id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
                nutrient_name TEXT NOT NULL,
                value DOUBLE PRECISION NOT NULL,
                unit TEXT NOT NULL,
                PRIMARY KEY (food_log_id, nutrient_name)
            );

            -- One row per (user, nutrient). Seeded from DRI defaults on
            -- account setup; is_custom distinguishes a user override from
            -- the still-current DRI default, so re-running the DRI seed
            -- (e.g. if age/sex profile changes) can update only the
            -- non-overridden rows without clobbering explicit user choices.
            CREATE TABLE IF NOT EXISTS nutrition_targets (
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
                nutrient_name TEXT NOT NULL,
                unit TEXT NOT NULL,
                daily_target DOUBLE PRECISION,
                max_threshold DOUBLE PRECISION,
                is_custom BOOLEAN NOT NULL DEFAULT FALSE,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (user_id, nutrient_name)
            );

            -- One row per user. mode="fixed": calorie_target/protein_g/
            -- carbs_g/fat_g are absolute daily targets. mode="ratio":
            -- calorie_target + protein_pct/carbs_pct/fat_pct (must sum to
            -- 100), grams are derived (protein/carbs = 4 kcal/g, fat =
            -- 9 kcal/g) rather than stored, so they stay in sync if the
            -- calorie target changes — same behavior Cronometer's "Macro
            -- Ratios" mode has.
            CREATE TABLE IF NOT EXISTS macro_target_settings (
                user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
                mode TEXT NOT NULL DEFAULT 'fixed',
                calorie_target DOUBLE PRECISION,
                protein_g DOUBLE PRECISION,
                carbs_g DOUBLE PRECISION,
                fat_g DOUBLE PRECISION,
                protein_pct DOUBLE PRECISION,
                carbs_pct DOUBLE PRECISION,
                fat_pct DOUBLE PRECISION,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );

            -- Profile fields for DRI lookup + sex-based water goal default.
            -- One row per user, created/updated via account setup.
            CREATE TABLE IF NOT EXISTS user_profile (
                user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
                age INTEGER,
                sex TEXT,
                height_cm DOUBLE PRECISION,
                weight_kg DOUBLE PRECISION,
                activity_level TEXT,
                water_target_ml DOUBLE PRECISION,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );

            -- Quick-add drinking water log. Append-only, same pattern as
            -- food_log/daily_nutrition — daily total is SUM(amount_ml).
            CREATE TABLE IF NOT EXISTS water_log (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
                date TEXT NOT NULL,
                amount_ml DOUBLE PRECISION NOT NULL,
                logged_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );

            -- Plain-text diary notes, one row per user per date (edits
            -- overwrite, unlike food_log's append-only model — a note is a
            -- single freeform entry for the day, not a list of items).
            -- attachment_url is nullable and unused today; reserved for a
            -- future photo-notes feature (object storage, e.g. GCS) without
            -- needing a schema migration when that's built.
            CREATE TABLE IF NOT EXISTS diary_notes (
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
                date TEXT NOT NULL,
                text TEXT NOT NULL,
                attachment_url TEXT,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (user_id, date)
            );

            CREATE INDEX IF NOT EXISTS idx_nutrition_metric ON daily_nutrition(user_id, metric, date);
            CREATE INDEX IF NOT EXISTS idx_orm_exercise ON lift_orm(user_id, exercise, date);
            CREATE INDEX IF NOT EXISTS idx_food_log_date ON food_log(user_id, date);
            CREATE INDEX IF NOT EXISTS idx_water_log_date ON water_log(user_id, date);

            -- TDEE/BMR tracking data. Was previously a local CSV file
            -- (app_data/user_{id}/tdee_tracking_log.csv) baked into the
            -- container image with no persistent volume mount -- writes
            -- during a request lived only as long as that container
            -- instance, so BMR was computed against partial/reset
            -- history depending on which instance handled the request.
            -- This table is the fix: same one-row-per-(user,date) shape
            -- as the CSV, but durable, matching every other table here.
            CREATE TABLE IF NOT EXISTS tdee_log (
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
                date TEXT NOT NULL,
                weight_lbs DOUBLE PRECISION,
                calories_consumed DOUBLE PRECISION,
                active_calories_burned DOUBLE PRECISION,
                PRIMARY KEY (user_id, date)
            );

            CREATE INDEX IF NOT EXISTS idx_tdee_log_date ON tdee_log(user_id, date);

            -- Pantry/fridge inventory. tracking_mode discriminates three
            -- "how much do I have" semantics rather than three separate
            -- tables (see nutrition-diary-design.md for the full design
            -- rationale): 'countable' (real decrementing serving count),
            -- 'bulk' (presence-only, until explicitly marked finished),
            -- 'single' (one item, no partial state — consuming it deletes
            -- the row outright). Links to the same food database food_log
            -- uses (source/source_id) so a pantry item never duplicates a
            -- food's canonical definition elsewhere -- but it DOES store
            -- its own nutrition data (calories/protein/carbs/fat/fiber +
            -- pantry_item_nutrients below), PER serving_size/serving_unit,
            -- the same "reference amount + scale by count" convention
            -- custom_foods uses. This was NOT true originally (a pantry
            -- item stored zero nutrition, relying on the caller to
            -- resupply it at /consume time, which the frontend never did
            -- -- consuming anything silently logged 0 macros to the
            -- diary). Fixed per explicit user request: removing/eating a
            -- pantry item must reflect real nutrition in the diary
            -- without the caller re-entering it.
            CREATE TABLE IF NOT EXISTS pantry_items (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
                food_name TEXT NOT NULL,
                source TEXT,
                source_id TEXT,
                serving_size DOUBLE PRECISION DEFAULT 1.0,
                serving_unit TEXT DEFAULT 'serving',
                tracking_mode TEXT NOT NULL DEFAULT 'countable',
                remaining_servings DOUBLE PRECISION,
                is_finished BOOLEAN NOT NULL DEFAULT FALSE,
                expiration_date TEXT,
                calories DOUBLE PRECISION DEFAULT 0,
                protein DOUBLE PRECISION DEFAULT 0,
                carbs DOUBLE PRECISION DEFAULT 0,
                fat DOUBLE PRECISION DEFAULT 0,
                fiber DOUBLE PRECISION DEFAULT 0,
                nutrients_json TEXT,
                added_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );

            -- pantry_items predates the nutrition columns above -- ALTER
            -- needed since CREATE TABLE IF NOT EXISTS is a no-op against
            -- an already-existing table (learned the hard way earlier
            -- this project via the user_preferences columns bug).
            ALTER TABLE pantry_items ADD COLUMN IF NOT EXISTS calories DOUBLE PRECISION DEFAULT 0;
            ALTER TABLE pantry_items ADD COLUMN IF NOT EXISTS protein DOUBLE PRECISION DEFAULT 0;
            ALTER TABLE pantry_items ADD COLUMN IF NOT EXISTS carbs DOUBLE PRECISION DEFAULT 0;
            ALTER TABLE pantry_items ADD COLUMN IF NOT EXISTS fat DOUBLE PRECISION DEFAULT 0;
            ALTER TABLE pantry_items ADD COLUMN IF NOT EXISTS fiber DOUBLE PRECISION DEFAULT 0;
            ALTER TABLE pantry_items ADD COLUMN IF NOT EXISTS nutrients_json TEXT;

            CREATE INDEX IF NOT EXISTS idx_pantry_items_user ON pantry_items(user_id);
            CREATE INDEX IF NOT EXISTS idx_pantry_items_expiration ON pantry_items(user_id, expiration_date)
                WHERE expiration_date IS NOT NULL;

            -- Mirrors custom_food_nutrients/food_log_nutrients -- one row
            -- per (pantry_item, nutrient), PER serving_size/serving_unit,
            -- instead of only relying on the nutrients_json cache column.
            CREATE TABLE IF NOT EXISTS pantry_item_nutrients (
                pantry_item_id INTEGER NOT NULL REFERENCES pantry_items(id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
                nutrient_name TEXT NOT NULL,
                value DOUBLE PRECISION NOT NULL,
                unit TEXT NOT NULL,
                PRIMARY KEY (pantry_item_id, nutrient_name)
            );

            -- User-defined foods with manually entered nutrients. Slots
            -- into the exact same "food reference" shape USDA/CNF results
            -- already use everywhere (source='custom', source_id=this
            -- table's id) — food_log, pantry_items, recipe_items, and
            -- meal_items all reference a custom food the same way they'd
            -- reference a USDA food, no special-casing needed downstream.
            -- Nutrients stored per a reference amount (reference_grams,
            -- nullable — a food with no known gram weight can only be
            -- scaled by multiple, not by gram amount, same distinction
            -- portion_scaling.py's two modes already capture).
            CREATE TABLE IF NOT EXISTS custom_foods (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
                food_name TEXT NOT NULL,
                brand TEXT,
                reference_amount DOUBLE PRECISION NOT NULL DEFAULT 1.0,
                reference_unit TEXT NOT NULL DEFAULT 'serving',
                reference_grams DOUBLE PRECISION,
                calories DOUBLE PRECISION DEFAULT 0,
                protein DOUBLE PRECISION DEFAULT 0,
                carbs DOUBLE PRECISION DEFAULT 0,
                fat DOUBLE PRECISION DEFAULT 0,
                fiber DOUBLE PRECISION DEFAULT 0,
                nutrients_json TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );

            -- Mirrors food_log_nutrients' normalization rationale — one
            -- row per (custom_food, nutrient) instead of an unread JSON
            -- blob, so recipe/meal aggregation can SUM across rows.
            CREATE TABLE IF NOT EXISTS custom_food_nutrients (
                custom_food_id INTEGER NOT NULL REFERENCES custom_foods(id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
                nutrient_name TEXT NOT NULL,
                value DOUBLE PRECISION NOT NULL,
                unit TEXT NOT NULL,
                PRIMARY KEY (custom_food_id, nutrient_name)
            );

            CREATE INDEX IF NOT EXISTS idx_custom_foods_user ON custom_foods(user_id);

            -- Recipes: aggregate items + servings-per-batch, so logging
            -- "1 serving" of a recipe divides the aggregated total by
            -- servings_per_batch. Distinct from meals (see meals below) --
            -- a recipe's whole point is batch division, a meal has none.
            CREATE TABLE IF NOT EXISTS recipes (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
                name TEXT NOT NULL,
                servings_per_batch DOUBLE PRECISION NOT NULL DEFAULT 1.0,
                source TEXT,
                source_id TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );

            ALTER TABLE recipes ADD COLUMN IF NOT EXISTS source TEXT;
            ALTER TABLE recipes ADD COLUMN IF NOT EXISTS source_id TEXT;

            -- One row per ingredient in a recipe. source/source_id mirrors
            -- food_log's convention (USDA/CNF/custom) — a recipe ingredient
            -- is just a food reference + an amount, same shape used
            -- everywhere else. amount_grams is nullable for the same
            -- gram-vs-multiple reason as custom_foods.reference_grams.
            CREATE TABLE IF NOT EXISTS recipe_items (
                id SERIAL PRIMARY KEY,
                recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
                food_name TEXT NOT NULL,
                source TEXT,
                source_id TEXT,
                amount_grams DOUBLE PRECISION,
                amount_multiple DOUBLE PRECISION,
                calories DOUBLE PRECISION DEFAULT 0,
                protein DOUBLE PRECISION DEFAULT 0,
                carbs DOUBLE PRECISION DEFAULT 0,
                fat DOUBLE PRECISION DEFAULT 0,
                fiber DOUBLE PRECISION DEFAULT 0,
                nutrients_json TEXT
            );

            CREATE TABLE IF NOT EXISTS recipe_item_nutrients (
                recipe_item_id INTEGER NOT NULL REFERENCES recipe_items(id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
                nutrient_name TEXT NOT NULL,
                value DOUBLE PRECISION NOT NULL,
                unit TEXT NOT NULL,
                PRIMARY KEY (recipe_item_id, nutrient_name)
            );

            CREATE INDEX IF NOT EXISTS idx_recipes_user ON recipes(user_id);
            CREATE INDEX IF NOT EXISTS idx_recipe_items_recipe ON recipe_items(recipe_id);

            -- Meals: a simple named collection of items, logged together
            -- at face value -- no batch/serving division, unlike recipes.
            CREATE TABLE IF NOT EXISTS meals (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
                name TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );

            CREATE TABLE IF NOT EXISTS meal_items (
                id SERIAL PRIMARY KEY,
                meal_id INTEGER NOT NULL REFERENCES meals(id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
                food_name TEXT NOT NULL,
                source TEXT,
                source_id TEXT,
                serving_size DOUBLE PRECISION DEFAULT 1.0,
                serving_unit TEXT DEFAULT 'serving',
                calories DOUBLE PRECISION DEFAULT 0,
                protein DOUBLE PRECISION DEFAULT 0,
                carbs DOUBLE PRECISION DEFAULT 0,
                fat DOUBLE PRECISION DEFAULT 0,
                fiber DOUBLE PRECISION DEFAULT 0,
                nutrients_json TEXT
            );

            CREATE TABLE IF NOT EXISTS meal_item_nutrients (
                meal_item_id INTEGER NOT NULL REFERENCES meal_items(id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
                nutrient_name TEXT NOT NULL,
                value DOUBLE PRECISION NOT NULL,
                unit TEXT NOT NULL,
                PRIMARY KEY (meal_item_id, nutrient_name)
            );

            CREATE INDEX IF NOT EXISTS idx_meals_user ON meals(user_id);
            CREATE INDEX IF NOT EXISTS idx_meal_items_meal ON meal_items(meal_id);

            -- User-configurable display preferences. One row per user.
            -- colors_json holds a flat {key: "#hexcolor"} map (macro
            -- segment colors + micronutrient status colors) rather than
            -- dedicated columns per color -- the set of colorable things
            -- is a frontend/display concern that will likely grow (new
            -- chart types, new status categories) and colors_json avoids
            -- a schema migration every time a new colorable element is
            -- added. sufficiency_threshold_pct is a real numeric setting
            -- (not just a color), broken out as its own column since it's
            -- used in actual threshold math, not just rendering.
            CREATE TABLE IF NOT EXISTS user_preferences (
                user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
                colors_json TEXT,
                sufficiency_threshold_pct DOUBLE PRECISION,
                unit_system TEXT,
                macro_chart_style TEXT,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );

            -- Additive columns for tables that existed before these
            -- fields did — CREATE TABLE IF NOT EXISTS is a no-op against
            -- an already-existing table, so new columns need an explicit
            -- ALTER TABLE the first time they're introduced.
            ALTER TABLE user_preferences ADD COLUMN IF NOT EXISTS unit_system TEXT;
            ALTER TABLE user_preferences ADD COLUMN IF NOT EXISTS macro_chart_style TEXT;
            -- User-picked nutrient names for the "Important to me"
            -- micronutrient card (see app/nutrient_groups.py) -- a plain
            -- JSON array of nutrient_name strings, same
            -- store-as-JSON-text convention colors_json already uses on
            -- this table. NULL/absent means "use a starter preset,"
            -- not "show nothing" -- resolved at read time, not written
            -- eagerly, so future starter-preset changes still reach
            -- users who never customized their list.
            ALTER TABLE user_preferences ADD COLUMN IF NOT EXISTS important_nutrients_json TEXT;

            -- Two independent sync pointers, per user, for the two-way
            -- Cronometer sync: last_pulled_at tracks how far the
            -- Cronometer -> TrackStack diary import has progressed (used
            -- to only import entries newer than the last successful
            -- sync, instead of re-parsing the full export range every
            -- time -- fixes the duplicate-import issue flagged when the
            -- read path was first built). last_pushed_at tracks how far
            -- the TrackStack -> Cronometer push has progressed (used to
            -- find food_log entries logged since the last push that
            -- still need to go to Cronometer). Deliberately two separate
            -- timestamps, not one shared pointer -- the two directions
            -- run independently and can succeed/fail on different
            -- schedules (e.g. a pull succeeds but a push fails, or vice
            -- versa), so conflating them into one pointer would silently
            -- skip or re-process entries in whichever direction didn't
            -- actually run.
            CREATE TABLE IF NOT EXISTS cronometer_sync_state (
                user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
                last_pulled_at TIMESTAMPTZ,
                last_pushed_at TIMESTAMPTZ,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );

            -- Named exercise/activity entries -- Cronometer's "Exercise"
            -- diary tab equivalent (e.g. "Running, 30 min, 300 kcal"),
            -- distinct from lift_orm (Hevy's structured strength-training
            -- sets: exercise/weight/reps per set) and tdee_log (one
            -- aggregate active_calories_burned NUMBER per day with no
            -- per-activity detail at all). A user can have any number of
            -- named activity entries per day. `source` distinguishes
            -- manually-logged ('manual', this app's own log form) from
            -- synced-in entries ('Cronometer') -- same convention
            -- food_log.source already uses, so the future push-direction
            -- sync can filter on "not already synced from Cronometer"
            -- the same way food_log's Cronometer push logic will.
            CREATE TABLE IF NOT EXISTS exercise_log (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
                date TEXT NOT NULL,
                activity_name TEXT NOT NULL,
                duration_minutes DOUBLE PRECISION,
                calories_burned DOUBLE PRECISION NOT NULL DEFAULT 0,
                source TEXT NOT NULL DEFAULT 'manual',
                source_id TEXT,
                notes TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );

            CREATE INDEX IF NOT EXISTS idx_exercise_log_user_date ON exercise_log(user_id, date);
        """)


async def close_db():
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None

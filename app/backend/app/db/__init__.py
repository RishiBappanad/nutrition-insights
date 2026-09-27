import os
from typing import Optional
import asyncpg
from cryptography.fernet import Fernet
from dotenv import load_dotenv

from .sql_builder import (
    validate_identifier, where_clause, insert_clause, update_clause,
    delete_clause, set_clause, returning_clause,
)

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
                nutrients_json TEXT
            );

            -- Additive column for the TrackStack -> Cronometer push sync
            -- pointer (cronometer_sync_state.last_pushed_at) to find
            -- entries created since the last push -- food_log predates
            -- this feature and had no creation timestamp at all.
            ALTER TABLE food_log ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now();

            -- Food-type category (produce/protein/dairy/etc, see
            -- app/food_category.py) -- distinct from `meal` above
            -- (Breakfast/Lunch/Dinner/Snack, when it was eaten). Nullable:
            -- an item with no source category data honestly has none,
            -- not a fabricated default.
            ALTER TABLE food_log ADD COLUMN IF NOT EXISTS category TEXT;

            -- Normalized per-nutrient breakdown for ANY loggable item that
            -- needs one -- a food_log entry, a pantry item, a custom food,
            -- a recipe/meal item, and whatever's added later. Replaces 5
            -- byte-for-byte identical tables (food_log_nutrients,
            -- pantry_item_nutrients, custom_food_nutrients,
            -- recipe_item_nutrients, meal_item_nutrients), each with its
            -- own hand-rolled read/write SQL scattered across 5 router
            -- files -- see the one-time migration below and
            -- app/nutrient_facts.py, which is now the ONLY code that reads
            -- or writes this table. owner_type is a plain string
            -- discriminator, not a foreign key (Postgres has no
            -- polymorphic FK across 5 different parent tables in one
            -- column) -- referential integrity is enforced in application
            -- code instead: every DELETE of an owning row, or a bulk
            -- replace of its items, must also call
            -- nutrient_facts.delete_*() for it. See OWNER_TYPES in
            -- app/nutrient_facts.py for the full list of valid values.
            CREATE TABLE IF NOT EXISTS nutrient_facts (
                owner_type TEXT NOT NULL,
                owner_id INTEGER NOT NULL,
                nutrient_name TEXT NOT NULL,
                value DOUBLE PRECISION NOT NULL,
                unit TEXT NOT NULL,
                PRIMARY KEY (owner_type, owner_id, nutrient_name)
            );

            CREATE INDEX IF NOT EXISTS idx_nutrient_facts_owner ON nutrient_facts(owner_type, owner_id);

            -- nutrition_targets and macro_target_settings no longer exist here:
            -- unified into `goals` (see the one-time migration at the end of
            -- this script, and app/nutrition_targets.py). Deliberately NOT
            -- re-created with IF NOT EXISTS -- that would resurrect two
            -- empty tables on every startup after the migration drops them.

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
            -- its own nutrition data (calories + nutrient_facts,
            -- owner_type='pantry_item', which carries protein/carbs/
            -- fat/fiber and every other non-macro nutrient), PER
            -- serving_size/serving_unit,
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
                nutrients_json TEXT,
                added_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );

            -- pantry_items predates the nutrition columns above -- ALTER
            -- needed since CREATE TABLE IF NOT EXISTS is a no-op against
            -- an already-existing table (learned the hard way earlier
            -- this project via the user_preferences columns bug).
            ALTER TABLE pantry_items ADD COLUMN IF NOT EXISTS calories DOUBLE PRECISION DEFAULT 0;
            ALTER TABLE pantry_items ADD COLUMN IF NOT EXISTS nutrients_json TEXT;
            ALTER TABLE pantry_items ADD COLUMN IF NOT EXISTS category TEXT;

            CREATE INDEX IF NOT EXISTS idx_pantry_items_user ON pantry_items(user_id);
            CREATE INDEX IF NOT EXISTS idx_pantry_items_expiration ON pantry_items(user_id, expiration_date)
                WHERE expiration_date IS NOT NULL;

            -- Per-nutrient breakdown for a pantry item lives in
            -- nutrient_facts (owner_type='pantry_item') -- see that
            -- table's own comment above.

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
                nutrients_json TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );

            -- Per-nutrient breakdown for a custom food lives in
            -- nutrient_facts (owner_type='custom_food').

            ALTER TABLE custom_foods ADD COLUMN IF NOT EXISTS category TEXT;

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

            -- Resolved food-type category for the recipe as a whole.
            -- Defaults to whichever category contributes the most total
            -- calories across the recipe's items (see
            -- food_category.dominant_category_by_calories) -- explicit
            -- user decision, 2026-09-08, over weight- or count-based
            -- alternatives. category_is_custom mirrors
            -- nutrition_targets.is_custom's pattern: distinguishes "user
            -- explicitly picked this" from "still the auto-computed
            -- default," so editing the recipe's ingredients later only
            -- recomputes the default when the user never overrode it.
            ALTER TABLE recipes ADD COLUMN IF NOT EXISTS category TEXT;
            ALTER TABLE recipes ADD COLUMN IF NOT EXISTS category_is_custom BOOLEAN NOT NULL DEFAULT FALSE;

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
                nutrients_json TEXT
            );

            -- Per-nutrient breakdown for a recipe item lives in
            -- nutrient_facts (owner_type='recipe_item').

            -- Each ingredient's own category (produce/protein/etc, from
            -- its source's raw category if it has one) -- this is what
            -- recipes.category's dominant-by-calories default is computed
            -- from, not user-facing on its own.
            ALTER TABLE recipe_items ADD COLUMN IF NOT EXISTS category TEXT;

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

            -- Same category/category_is_custom pattern as recipes above --
            -- meals are structurally the same "composite of items" shape,
            -- so they get the same dominant-by-calories default treatment.
            ALTER TABLE meals ADD COLUMN IF NOT EXISTS category TEXT;
            ALTER TABLE meals ADD COLUMN IF NOT EXISTS category_is_custom BOOLEAN NOT NULL DEFAULT FALSE;

            CREATE TABLE IF NOT EXISTS meal_items (
                id SERIAL PRIMARY KEY,
                meal_id INTEGER NOT NULL REFERENCES meals(id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
                food_name TEXT NOT NULL,
                source TEXT,
                source_id TEXT,
                serving_size DOUBLE PRECISION DEFAULT 1.0,
                serving_unit TEXT DEFAULT 'serving',
                calories DOUBLE PRECISION DEFAULT 0,
                nutrients_json TEXT
            );

            ALTER TABLE meal_items ADD COLUMN IF NOT EXISTS category TEXT;

            -- Per-nutrient breakdown for a meal item lives in
            -- nutrient_facts (owner_type='meal_item').

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

            -- Universal Event Contract's actual backing store (2026-09-14),
            -- generalizing todo-tracker's todo_events pattern (one
            -- append-only event log per tracker) across every domain
            -- entity this tracker owns, rather than the previous approach
            -- of GET /events deriving events live from food_log/
            -- exercise_log's current rows -- which could only ever show
            -- "this exists," never "this was updated" or "this was
            -- deleted after the fact." One polymorphic table (owner_type,
            -- owner_id) + one shared helper (app/domain_events.py) that
            -- every mutating route calls into, same reasoning
            -- CLAUDE.md's nutrient_facts pattern already documents for
            -- avoiding N hand-rolled per-entity tables. event_type keeps
            -- the Core Event Shape's existing "<entity>_<action>"
            -- convention todo-tracker established (e.g. "food_log_created",
            -- "pantry_item_deleted"), not a repurposed generic action enum.
            -- occurred_at is the entity's own business date where one
            -- exists (food_log.date, a user can backdate it) and defaults
            -- to insert time otherwise (pantry items, todos); logged_at is
            -- always insert time regardless -- keeping the two separate is
            -- what lets the Core Event Shape's occurred_at/created_at pair
            -- mean different things instead of always collapsing to one
            -- value the way a naive DEFAULT now() on a single column would.
            CREATE TABLE IF NOT EXISTS domain_events (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
                owner_type TEXT NOT NULL,
                owner_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                category TEXT,
                amount DOUBLE PRECISION NOT NULL DEFAULT 0,
                label TEXT,
                source TEXT,
                source_id TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                logged_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );

            CREATE INDEX IF NOT EXISTS idx_domain_events_user_id ON domain_events(user_id);
            CREATE INDEX IF NOT EXISTS idx_domain_events_owner ON domain_events(owner_type, owner_id);

            -- A saved comparison -- measure_query checked against a
            -- reference (a plain constant OR itself a query over a
            -- different time window) via comparator. Pure definition:
            -- no last_status/last_evaluated_period_start columns, same
            -- "no persisted evaluation state" decision finance-tracker's
            -- own goals table made (see
            -- workspace-notes/RECURRING_AND_GOALS_SPEC.md) -- compliance
            -- is computed live from domain_events' own history every
            -- time, via app/goals_evaluation.py. measure_query/
            -- reference_query are JSON-encoded TEXT, this codebase's
            -- established convention for every JSON-shaped column (see
            -- domain_events.metadata_json above), not a native JSONB
            -- column. No UNIQUE constraint on the query shape -- the old
            -- flat-schema design's UNIQUE(user_id, category, period,
            -- severity) doesn't survive this generalization either, same
            -- as finance-tracker's version.
            CREATE TABLE IF NOT EXISTS goals (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
                label TEXT,
                severity TEXT NOT NULL DEFAULT 'target',
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                comparator TEXT NOT NULL,
                tolerance_percent DOUBLE PRECISION,
                measure_query TEXT NOT NULL,
                reference_amount DOUBLE PRECISION,
                reference_query TEXT,
                inflation_adjusted BOOLEAN NOT NULL DEFAULT FALSE,
                notify_on_crossing BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );

            CREATE INDEX IF NOT EXISTS idx_goals_user_active ON goals(user_id, is_active);

            -- Who/what created this goal. NULL = user-authored via the
            -- Goals/Targets UI. 'dri_default' = auto-seeded DRI nutrient
            -- target (refreshed on profile change, never touched once the
            -- user customizes it -> 'user_target'). 'macro_target' = the
            -- calorie/protein/carbs/fat rows written by PUT /targets/macros.
            -- Additive, generically named column -- nothing about it is
            -- nutrition-specific, so another tracker could use it too.
            ALTER TABLE goals ADD COLUMN IF NOT EXISTS source TEXT;

            -- Long-Term goals only (goal_query.py's term_of() == 'long_term' --
            -- an all_time/fixed_range measure, e.g. a vital goal): an optional
            -- deadline (target_date) and a one-time snapshot of the measure's
            -- value at goal-creation time (start_value), used to draw a
            -- start -> current -> target progress bar instead of the plain
            -- current/target bar Everyday goals use. Both null for every
            -- Everyday goal and for any Long-Term goal that predates this
            -- column (no snapshot to backfill -- the bar just falls back to
            -- the plain style until one exists). Immutable once set except by
            -- an explicit user edit (PATCH), never recomputed by anything else.
            -- TEXT, not a native DATE column -- this codebase's established
            -- convention for every business date (food_log.date,
            -- daily_nutrition.date, ...), specifically to avoid asyncpg's
            -- strict native-date/timestamp typing (a bare Python str isn't
            -- accepted for a real date column without an explicit parse,
            -- the same friction domain_events.py's occurred_at already
            -- works around).
            ALTER TABLE goals ADD COLUMN IF NOT EXISTS target_date TEXT;
            ALTER TABLE goals ADD COLUMN IF NOT EXISTS start_value DOUBLE PRECISION;

            -- logged_at was added a short time after domain_events itself
            -- (still pre-launch, no real rows anywhere yet) to separate
            -- "row insert time" from occurred_at once occurred_at started
            -- carrying a real backdatable business date -- IF NOT EXISTS
            -- guard so this is a no-op everywhere the column already
            -- exists, same convention as every other migration below.
            ALTER TABLE domain_events ADD COLUMN IF NOT EXISTS logged_at TIMESTAMPTZ NOT NULL DEFAULT now();

            -- One-time migration (2026-08-27): fiber and protein/carbs/fat
            -- used to be denormalized macro columns on food_log/
            -- pantry_items/custom_foods/recipe_items/meal_items, backfilled
            -- into their respective X_nutrients child table and dropped in
            -- two earlier migrations the same day. Those two migrations are
            -- now themselves fully applied everywhere (verified: zero
            -- fiber/protein/carbs/fat columns remain on any of these 5
            -- tables in production) and have been removed from this file --
            -- their guard conditions could never fire again. This
            -- migration is the next step in the same lineage: the 5
            -- X_nutrients child tables that backfill target was standardized
            -- into (food_log_nutrients, pantry_item_nutrients,
            -- custom_food_nutrients, recipe_item_nutrients,
            -- meal_item_nutrients) were byte-for-byte identical tables with
            -- 6 independent hand-rolled read implementations and 2
            -- independent (one a hand-duplicate) write implementations
            -- scattered across 5 router files -- the root cause of every
            -- nutrient-related change needing to touch 5+ files. Collapsed
            -- into the single nutrient_facts table above; app/
            -- nutrient_facts.py is now the only code that reads or writes
            -- it. Idempotent: each block only runs once (guarded by
            -- checking the OLD table still exists), safe on every startup.
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'food_log_nutrients') THEN
                    INSERT INTO nutrient_facts (owner_type, owner_id, nutrient_name, value, unit)
                    SELECT 'food_log', food_log_id, nutrient_name, value, unit FROM food_log_nutrients
                    ON CONFLICT (owner_type, owner_id, nutrient_name) DO NOTHING;
                    DROP TABLE food_log_nutrients;
                END IF;

                IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'pantry_item_nutrients') THEN
                    INSERT INTO nutrient_facts (owner_type, owner_id, nutrient_name, value, unit)
                    SELECT 'pantry_item', pantry_item_id, nutrient_name, value, unit FROM pantry_item_nutrients
                    ON CONFLICT (owner_type, owner_id, nutrient_name) DO NOTHING;
                    DROP TABLE pantry_item_nutrients;
                END IF;

                IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'custom_food_nutrients') THEN
                    INSERT INTO nutrient_facts (owner_type, owner_id, nutrient_name, value, unit)
                    SELECT 'custom_food', custom_food_id, nutrient_name, value, unit FROM custom_food_nutrients
                    ON CONFLICT (owner_type, owner_id, nutrient_name) DO NOTHING;
                    DROP TABLE custom_food_nutrients;
                END IF;

                IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'recipe_item_nutrients') THEN
                    INSERT INTO nutrient_facts (owner_type, owner_id, nutrient_name, value, unit)
                    SELECT 'recipe_item', recipe_item_id, nutrient_name, value, unit FROM recipe_item_nutrients
                    ON CONFLICT (owner_type, owner_id, nutrient_name) DO NOTHING;
                    DROP TABLE recipe_item_nutrients;
                END IF;

                IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'meal_item_nutrients') THEN
                    INSERT INTO nutrient_facts (owner_type, owner_id, nutrient_name, value, unit)
                    SELECT 'meal_item', meal_item_id, nutrient_name, value, unit FROM meal_item_nutrients
                    ON CONFLICT (owner_type, owner_id, nutrient_name) DO NOTHING;
                    DROP TABLE meal_item_nutrients;
                END IF;
            END $$;

            -- One-time migration (2026-09-24): Targets and Goals unified onto
            -- the `goals` table. nutrition_targets (per-nutrient DRI/custom
            -- daily_target + max_threshold) and macro_target_settings
            -- (calorie/macro targets, fixed or ratio) become goals rows in
            -- the Goal Query shape -- a floor (gte) and a ceiling (lte) goal
            -- per nutrient, and calorie/protein/carbs/fat goals for macros --
            -- then both tables are dropped. Ratio-mode macros are resolved to
            -- concrete grams at migration time (same math as
            -- nutrition_targets.derive_macro_grams).
            --
            -- Also backfills domain_events for food_log rows that predate
            -- the event log (22k+ of them at migration time): goals/targets
            -- now read consumption from domain_events, so without this every
            -- historical date would read as zero. Backfilled events use
            -- occurred_at = the entry's own date at UTC midnight and
            -- logged_at = its created_at, matching what log_domain_event
            -- writes for a live entry.
            --
            -- Idempotent and race-safe: an advisory lock serializes
            -- concurrent startups (Cloud Run can boot several instances at
            -- once), the existence check runs AFTER the lock, and the DROPs
            -- at the end make every later startup a no-op.
            DO $$
            BEGIN
                PERFORM pg_advisory_xact_lock(872001);

                IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'nutrition_targets') THEN
                    INSERT INTO domain_events
                        (user_id, owner_type, owner_id, event_type, category, amount, label, source, source_id, metadata_json, occurred_at, logged_at)
                    SELECT fl.user_id, 'food_log', fl.id, 'food_log_created', fl.category, COALESCE(fl.calories, 0),
                           fl.food_name, fl.source, fl.source_id, jsonb_build_object('meal', fl.meal)::text,
                           (fl.date::date)::timestamp AT TIME ZONE 'UTC', fl.created_at
                    FROM food_log fl
                    WHERE fl.date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
                      AND NOT EXISTS (SELECT 1 FROM domain_events de WHERE de.owner_type = 'food_log' AND de.owner_id = fl.id);

                    -- Repair: events already in the log whose occurred_at
                    -- doesn't match their food_log entry's own date. Pantry
                    -- "consume" logged its event without the entry's date
                    -- (occurred_at defaulted to click time), and some events
                    -- were written at host-local rather than UTC midnight.
                    -- Consumption is bucketed by occurred_at, so both would
                    -- misfile entries by a day (or, for the pantry case, by
                    -- years when backdated/future-dated).
                    UPDATE domain_events de
                    SET occurred_at = (fl.date::date)::timestamp AT TIME ZONE 'UTC'
                    FROM food_log fl
                    WHERE de.owner_type = 'food_log' AND de.owner_id = fl.id
                      AND fl.date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
                      AND de.occurred_at <> (fl.date::date)::timestamp AT TIME ZONE 'UTC';

                    INSERT INTO goals (user_id, label, severity, comparator, measure_query, reference_amount, notify_on_crossing, source)
                    SELECT nt.user_id, nt.nutrient_name, 'target', 'gte',
                           jsonb_build_object(
                               'aggregation', 'sum',
                               'filters', jsonb_build_array(jsonb_build_object('field', 'owner_type', 'operator', 'eq', 'value', 'food_log')),
                               'timeWindow', jsonb_build_object('kind', 'current_period', 'period', 'daily'),
                               'measureField', 'nutrient:' || nt.nutrient_name)::text,
                           nt.daily_target, FALSE, CASE WHEN nt.is_custom THEN 'user_target' ELSE 'dri_default' END
                    FROM nutrition_targets nt WHERE nt.daily_target IS NOT NULL;

                    INSERT INTO goals (user_id, label, severity, comparator, measure_query, reference_amount, notify_on_crossing, source)
                    SELECT nt.user_id, nt.nutrient_name || ' (max)', 'target', 'lte',
                           jsonb_build_object(
                               'aggregation', 'sum',
                               'filters', jsonb_build_array(jsonb_build_object('field', 'owner_type', 'operator', 'eq', 'value', 'food_log')),
                               'timeWindow', jsonb_build_object('kind', 'current_period', 'period', 'daily'),
                               'measureField', 'nutrient:' || nt.nutrient_name)::text,
                           nt.max_threshold, FALSE, CASE WHEN nt.is_custom THEN 'user_target' ELSE 'dri_default' END
                    FROM nutrition_targets nt WHERE nt.max_threshold IS NOT NULL;

                    DROP TABLE nutrition_targets;
                END IF;

                IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'macro_target_settings') THEN
                    INSERT INTO goals (user_id, label, severity, comparator, tolerance_percent, measure_query, reference_amount, notify_on_crossing, source)
                    SELECT m.user_id, 'Calories', 'target', 'within_tolerance_percent', 10,
                           jsonb_build_object(
                               'aggregation', 'sum',
                               'filters', jsonb_build_array(jsonb_build_object('field', 'owner_type', 'operator', 'eq', 'value', 'food_log')),
                               'timeWindow', jsonb_build_object('kind', 'current_period', 'period', 'daily'))::text,
                           m.calorie_target, FALSE, 'macro_target'
                    FROM macro_target_settings m WHERE m.calorie_target IS NOT NULL;

                    INSERT INTO goals (user_id, label, severity, comparator, tolerance_percent, measure_query, reference_amount, notify_on_crossing, source)
                    SELECT m.user_id, v.label, 'target', v.comparator, v.tolerance,
                           jsonb_build_object(
                               'aggregation', 'sum',
                               'filters', jsonb_build_array(jsonb_build_object('field', 'owner_type', 'operator', 'eq', 'value', 'food_log')),
                               'timeWindow', jsonb_build_object('kind', 'current_period', 'period', 'daily'),
                               'measureField', v.measure_field)::text,
                           v.grams, FALSE, 'macro_target'
                    FROM macro_target_settings m
                    CROSS JOIN LATERAL (VALUES
                        ('Protein', 'gte', NULL::double precision, 'nutrient:Protein',
                            CASE WHEN m.mode = 'ratio' THEN round((m.calorie_target * m.protein_pct / 100 / 4)::numeric, 1)::double precision ELSE m.protein_g END),
                        ('Carbs', 'within_tolerance_percent', 10::double precision, 'nutrient:Carbohydrate, by difference',
                            CASE WHEN m.mode = 'ratio' THEN round((m.calorie_target * m.carbs_pct / 100 / 4)::numeric, 1)::double precision ELSE m.carbs_g END),
                        ('Fat', 'within_tolerance_percent', 10::double precision, 'nutrient:Total lipid (fat)',
                            CASE WHEN m.mode = 'ratio' THEN round((m.calorie_target * m.fat_pct / 100 / 9)::numeric, 1)::double precision ELSE m.fat_g END)
                    ) AS v(label, comparator, tolerance, measure_field, grams)
                    WHERE v.grams IS NOT NULL;

                    DROP TABLE macro_target_settings;
                END IF;
            END $$;

            -- Vitals for Goals (2026-09-24). Weight already lives in
            -- daily_nutrition ("Weight (lbs)": Charts, Cronometer sync,
            -- POST /data/weight); to make a reading a goal-able domain_event
            -- (owner_type 'vital') each row needs an integer identity, which
            -- its composite primary key doesn't provide. A SERIAL column adds
            -- one (existing rows are numbered by the ALTER itself).
            ALTER TABLE daily_nutrition ADD COLUMN IF NOT EXISTS id SERIAL;
            CREATE UNIQUE INDEX IF NOT EXISTS idx_daily_nutrition_id ON daily_nutrition(id);

            -- Backfill one vital_created event per existing vital reading
            -- (occurred_at = the reading's own date at UTC midnight). Guarded
            -- by NOT EXISTS so every later startup is a no-op; the advisory
            -- lock keeps two booting instances from both inserting.
            DO $$
            BEGIN
                PERFORM pg_advisory_xact_lock(872002);
                INSERT INTO domain_events
                    (user_id, owner_type, owner_id, event_type, category, amount, label, source, metadata_json, occurred_at)
                SELECT dn.user_id, 'vital', dn.id, 'vital_created',
                       CASE dn.metric WHEN 'Weight (lbs)' THEN 'weight' WHEN 'Body Fat (%)' THEN 'body_fat' END,
                       dn.value, dn.metric, 'daily_nutrition', '{}',
                       (dn.date::date)::timestamp AT TIME ZONE 'UTC'
                FROM daily_nutrition dn
                WHERE dn.metric IN ('Weight (lbs)', 'Body Fat (%)')
                  AND dn.date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
                  AND NOT EXISTS (SELECT 1 FROM domain_events e WHERE e.owner_type = 'vital' AND e.owner_id = dn.id);
            END $$;

            -- One protein floor, not two (2026-09-24). A user who set macro
            -- targets also had the auto-seeded DRI protein floor: two goals
            -- asserting the same thing. The macro target is the one the user
            -- chose, so any DRI-sourced goal that duplicates a macro_target
            -- goal (same measure + comparator, daily food_log sum) is dropped.
            -- Idempotent: once removed there's nothing left to match.
            DELETE FROM goals d
            USING goals m
            WHERE d.user_id = m.user_id
              AND m.source = 'macro_target' AND d.source IN ('dri_default', 'user_target')
              AND d.comparator = m.comparator
              AND d.measure_query::jsonb = m.measure_query::jsonb;

        """)


async def close_db():
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def delete_with_ownership_returning(conn, table: str, row_id: int, user_id: int, returning: list[str]):
    """DELETE FROM <table> WHERE id = $1 AND user_id = $2 RETURNING
    <returning columns>, in one round trip -- the exact shape
    delete_pantry_item/finish_pantry_item/delete_food_entry each wrote out
    by hand identically (only the table and returned columns differed).
    Returns None if no matching row existed (wrong id, wrong owner, or
    both), same as the row simply not being there for the caller to log
    a domain event for.

    Built from sql_builder's composable clauses rather than an f-string
    -- `table` and `returning` still must only ever be hardcoded
    literals, never anything derived from request input, but
    sql_builder.validate_identifier now rejects anything else at the
    point of interpolation instead of relying solely on that convention."""
    sql = delete_clause(table) + where_clause(["id = $1", "user_id = $2"]) + returning_clause(returning)
    return await conn.fetchrow(sql, row_id, user_id)


async def insert_returning(conn, table: str, values: dict, returning: str = "id"):
    """INSERT INTO <table> (<values' keys>) VALUES (<values' values>)
    RETURNING <returning> -- the shape every create_* function in this
    codebase wrote out by hand with its own column list; this just
    builds that column list/placeholder list from a dict instead, so
    adding a column means changing the caller's dict, not counting
    placeholders by hand.

    `table`, the keys of `values`, and `returning` still must only ever
    be hardcoded literals, never anything derived from request input --
    validate_identifier (via insert_clause/returning_clause) enforces
    that at the point of interpolation. Only the VALUES themselves go
    through as real bound parameters."""
    columns = list(values.keys())
    sql = insert_clause(table, columns) + returning_clause([returning])
    return await conn.fetchval(sql, *values.values())


async def update_with_ownership_returning(conn, table: str, row_id: int, user_id: int, values: dict, returning: list[str]):
    """UPDATE <table> SET <col> = COALESCE($n, <col>), ..., updated_at =
    now() WHERE id = $.. AND user_id = $.. RETURNING <returning columns>.

    Each entry in `values` is a COALESCE-guarded partial update (a `None`
    value leaves that column unchanged) -- the same "only overwrite what
    was actually provided" convention every existing PATCH-style update
    in this codebase already followed by hand (e.g. pantry's
    PantryItemUpdateRequest). Returns None if no matching row existed.

    The COALESCE assignment fragments are still built here (not in
    sql_builder) since they need each column validated AND paired with
    its own placeholder number, which is specific to this partial-update
    shape rather than a general SET clause; set_clause just joins the
    already-validated fragments. `table` and `returning` go through
    validate_identifier the same as every other builder call."""
    set_parts = []
    params: list = []
    for column, value in values.items():
        validate_identifier(column)
        params.append(value)
        set_parts.append(f"{column} = COALESCE(${len(params)}, {column})")
    params.append(row_id)
    id_param = len(params)
    params.append(user_id)
    user_param = len(params)

    sql = (
        update_clause(table)
        + set_clause(set_parts + ["updated_at = now()"])
        + where_clause([f"id = ${id_param}", f"user_id = ${user_param}"])
        + returning_clause(returning)
    )
    return await conn.fetchrow(sql, *params)

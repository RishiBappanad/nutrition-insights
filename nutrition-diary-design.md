# Nutrition Diary + Targets — Design Proposal

Researched against Cronometer's actual feature docs (support.cronometer.com)
rather than from memory, per this project's verification tenets. Sources
cited inline. This is a proposal for sign-off before implementation — scope
is large enough that getting the schema wrong now is expensive to unwind
later (once real user data exists in it).

## API-first constraint (explicit user requirement)

Every piece of this feature must be a real, independently-callable backend
endpoint — not logic embedded only in frontend code. The frontend is one
consumer of the API, not the only one. This matters concretely for:
food logging, search, targets (get/set), water logging, notes, and account
profile fields — each needs a real REST endpoint with request/response
JSON, callable via a bare `curl`/Shortcuts-style request with just a Bearer
token, the same way every other route in this app already works. No
"compute this client-side and only POST the final number" shortcuts — e.g.
target-vs-actual progress should be computable via a GET endpoint, not
require the frontend to fetch raw entries and do the math itself.

This is a direct extension of the "iPhone Shortcuts" future-requirement
already noted in `trackstack-notes.md` — building it in from the start
here rather than retrofitting later.

## What Cronometer actually does (verified against their docs)

- **Diary** = a per-day log of 4 entry types: Food, Exercise, Biometrics,
  Notes. Food entries show timestamp, description, amount+unit, and one
  configurable nutrient column (defaults to Energy).
- **Targets** are two-tier:
  - **Macronutrient targets**: either "Fixed Values" (constant every day,
    defaults to DRI/RDA-based) or "Macro Ratios" (protein/carb/fat as % of
    a calorie target, recalculates automatically when calorie target
    changes) or auto-calculated from energy burned.
  - **Micronutrient targets**: every tracked micronutrient defaults to its
    RDA/AI (Recommended Dietary Allowance / Adequate Intake) from the USDA
    Dietary Reference Intakes, with an optional Maximum Threshold defaulted
    from the UL (Tolerable Upper Intake Level). Nutrients without an
    established DRI show "No Target." Every one of these defaults is
    user-editable individually ("toggle Use custom values instead of
    DRIs").
  - Targets vary by age/sex/life-stage per the DRI tables — Cronometer asks
    for this in profile setup to pick correct RDA defaults.
- **Diary summary sections**: Energy Summary, Macronutrient Summary,
  Nutrient Target Summary (per-micronutrient progress bars), Nutrition
  Scores (aggregate % of targets hit across categories), Nutrient Balance
  gauges (Omega 6:3, Zinc:Copper, Potassium:Sodium, Calcium:Magnesium,
  Calcium:Oxalate, PRAL).
- Also: water tracking widget, fasting tracker, custom biometrics,
  copy/paste diary entries between days, "add a note" with photos.

## What this means for our design

### 1. Schema: `food_log` needs to become nutrient-complete, not 5 columns

**Current state**: `daily_nutrition`/`food_log` (nutrition-insights,
Postgres, already migrated off SQLite earlier this session) stores exactly
`calories, protein, carbs, fat, fiber` per entry, plus a `nutrients_json`
text blob that's written but never read back structured.

**Verified data availability**: USDA FoodData Central returns ~87 nutrients
per food (confirmed via a live API call: full amino acid profile, all major
vitamins/minerals, fatty acid subtypes, sugars, etc.) — the ceiling isn't
the data source, it's that today's schema throws almost all of it away.

**Decision needed**: normalize `nutrients_json` into a real
`food_log_nutrients` table (`food_log_id, nutrient_name, value, unit`) so
per-nutrient totals can be aggregated with SQL (needed for the daily
progress dashboard), rather than a JSON blob that requires parsing every
row in application code. This is the same normalization decision already
made for `daily_nutrition`/`lift_orm` earlier — one row per (entity,
metric), not one wide row.

### 2. Targets: two new tables, mirroring Cronometer's two-tier model

- `nutrition_targets` (one row per user per nutrient): `user_id,
  nutrient_name, daily_target, max_threshold, is_custom, updated_at`.
  Seeded with DRI/RDA/UL defaults on account creation (needs a static
  DRI reference table/JSON bundled with the app — USDA publishes these
  as public tables, no API needed, they're static reference data).
- `macro_target_settings` (one row per user): `mode` (`fixed` |
  `ratio` | `auto_from_burn`), plus mode-specific fields (fixed:
  calories/protein_g/carbs_g/fat_g; ratio: calorie_target +
  protein_pct/carbs_pct/fat_pct).
- Per your instruction: **macros get the simple always-visible editor**
  (calories + 3 macro sliders/fields, matching Cronometer's default
  "Fixed Values" UX), **micronutrients get an "Advanced" section** that's
  collapsed by default, showing the full per-nutrient DRI-seeded list with
  individual override — this matches Cronometer's actual UX split (macro
  editing is one screen, micronutrient editing is a separate, denser
  settings section) rather than us inventing a new pattern.

### 3. Diary page: new frontend surface, backend mostly exists

- `GET /food/log?date=` already returns entries + totals for
  calories/protein/carbs/fat/fiber (existing code, `food.py`). Needs
  extending to return per-nutrient totals against the new
  `food_log_nutrients` table instead of the 5 hardcoded columns.
- New: `GET /nutrition/targets` (today's targets, resolved: custom
  override if set, else DRI default) and `GET /nutrition/progress?date=`
  (target vs. actual per nutrient, for progress bars).
- Frontend: a real diary page (search → pick from USDA/CNF result → log to
  a meal) + a dashboard section showing macro progress bars (always
  visible) and a micronutrient summary (collapsed/expandable, matching the
  "Advanced" targets split above).

### 4. Barcode + Open Food Facts — separate follow-up phase, not blocking this one

Flagging again since it's a new external dependency: Open Food Facts
(open, free, no API key, barcode-first, huge branded-product coverage) is
the natural source for barcode scans — USDA/CNF aren't barcode-indexed.
Sequenced after the diary/targets core lands, per the original phase plan.

### 5. Cronometer stays wired, becomes optional, not primary

`sync.py`/`cronometer_rpc.py` keep working as an *import* path (population
of the same `food_log`/`daily_nutrition` tables), not deprecated. The new
in-house diary becomes the primary day-to-day path. No changes needed to
the existing sync code for this phase — it already writes into the same
tables the new dashboard will read from.

## Decisions (user sign-off, 2026-07-19)

- **Nutrition Scores / Nutrient Balance gauges**: deferred. Not building
  in this phase.
- **DRI defaults**: account setup will collect age/sex/life-stage (and
  likely weight/height/activity level, since those already exist for TDEE
  calculation elsewhere in this app) so DRI-seeded targets are accurate per
  user, not a single generic default.
- **Water tracking**: in scope for this phase.
- **Fasting**: deferred — timestamp handling isn't validated yet elsewhere
  in this app, don't want to build a time-sensitive feature on an
  unvalidated foundation.
- **Notes with photos**: deferred — undecided storage/cost story for image
  hosting. Plain text notes (no photos) may still be in scope if useful,
  but no image attachment work in this phase.

### 6. Water tracking (verified against Cronometer's actual model)

- Cronometer tracks **drinking water only** (not water-content-from-food)
  as quick-add units (cups/oz/mL, user-configurable), with a daily goal
  that defaults by sex (48 fl oz / ~6 cups for female, 64 fl oz / ~8 cups
  for male) — this is the concrete reason account setup needs to collect
  sex, not just for DRI lookups.
- Schema: `water_log` (`user_id, date, amount_ml, logged_at`) — one row per
  add, same "append, don't overwrite" pattern as `food_log`. Daily total is
  `SUM(amount_ml) WHERE date = ...`, same shape as everything else here.
  `water_target_ml` lives in the per-user settings/profile row (or reuse
  `nutrition_targets` with a synthetic `"Water"` nutrient row — leaning
  towards the settings row since water isn't from the USDA nutrient set
  and doesn't need a DRI/UL pair, just one goal number).
- Frontend: a small widget (+/- quick-add, matching Cronometer's actual UI
  pattern) on the diary page, not a separate page.

## Pantry / fridge schema (added 2026-07-19, same session as diary schema)

User's requirements, verbatim intent: browse what's at home, log an item (or
part of one) directly to the diary from the pantry view, decrement/remove
it from pantry when consumed, track expiration dates, and support three
distinct "how much do I have" semantics:
1. **Countable servings** — a box with N servings, decrements per use.
2. **Bulk/indefinite** — a spice jar; don't track an exact remaining amount,
   just "I have it" until the user marks it finished.
3. **One-and-done** — a single item (one apple, one can) with no partial
   consumption — exists or doesn't.

### Design: `tracking_mode` discriminates the three cases, one table

Rejected a three-separate-tables design (countable/bulk/single) — the
actual difference between the three is just how `remaining_servings` is
interpreted, not a structurally different shape. One `pantry_items` table
with a `tracking_mode` column is simpler and is exactly the same "shared
shape, one discriminating column" pattern already used for
`macro_target_settings.mode` above.

```sql
CREATE TABLE pantry_items (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,

    -- Links to the same food database (USDA/CNF, same as food_log) so
    -- nutrition info is never re-entered — a pantry item IS a food_log-
    -- shaped reference, just sitting in inventory instead of logged yet.
    food_name TEXT NOT NULL,
    source TEXT,               -- "USDA" | "CNF" | "manual"
    source_id TEXT,            -- USDA fdcId / CNF food_code, if applicable
    serving_size DOUBLE PRECISION DEFAULT 1.0,
    serving_unit TEXT DEFAULT 'serving',

    tracking_mode TEXT NOT NULL DEFAULT 'countable',
    -- 'countable': remaining_servings is a real, decrementing count.
    -- 'bulk':      remaining_servings is NULL/ignored; presence alone
    --              matters until is_finished is set true.
    -- 'single':    remaining_servings is always 1 until consumed, then
    --              the row is deleted outright (see consumption flow
    --              below) rather than decremented to 0 and kept around.
    remaining_servings DOUBLE PRECISION,
    is_finished BOOLEAN NOT NULL DEFAULT FALSE,

    expiration_date TEXT,       -- nullable; same TEXT-date convention as food_log.date
    added_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_pantry_items_user ON pantry_items(user_id);
CREATE INDEX idx_pantry_items_expiration ON pantry_items(user_id, expiration_date)
    WHERE expiration_date IS NOT NULL;
```

### Consumption flow (pantry -> diary), the actual feature ask

"Open pantry, add an item or part of it to the diary, remove it from the
pantry if necessary" is one action, not two separate ones the frontend has
to sequence — needs one backend endpoint (per the API-first requirement)
that does both atomically:

`POST /pantry/{id}/consume` body `{ servings: number }`:
1. Insert a real `food_log` row (+ `food_log_nutrients`, scaled by
   `servings`) — reuses the exact same nutrient-scaling logic food logging
   already needs for `food_log`, not a separate code path.
2. Then, based on `tracking_mode`:
   - `countable`: `remaining_servings -= servings`; if it hits `<= 0`,
     delete the row (matches "remove from pantry if necessary" — fully
     consumed means gone, not a lingering zero row).
   - `bulk`: no quantity change at all. A separate, explicit
     `POST /pantry/{id}/finish` marks `is_finished = true` (or just
     deletes it — leaning towards delete-on-finish for the same "gone
     means gone" reasoning, no soft-delete state to manage).
   - `single`: any consumption at all deletes the row — there's no partial
     state for a single item by definition.

This means `pantry_items` doesn't need a `finish`/delete distinction
exposed as separate API concepts per mode — `POST /consume` and
`POST /finish` are the only two mutations, and the row's `tracking_mode`
decides what "consume" actually does to the count.

### Why this doesn't need its own nutrient-storage duplication

A pantry item is never itself the subject of nutrition math — it's
inventory metadata (what, how much, source, expiry) that resolves back to
the *same* USDA/CNF food entry `food_log` already uses. When consumed, the
nutrient breakdown is looked up fresh (or copied from a cached
`food_log_nutrients`-shaped payload the frontend already had from the
pantry-add flow) at consumption time, scaled by servings — not stored
redundantly on `pantry_items` itself. Keeps one source of truth for "what
are the nutrients in this food," matching the design principle already
applied to `food_log`/`food_log_nutrients`.

### API surface (all backend, API-first per explicit requirement)

- `GET /pantry` — list current items (excludes finished/deleted).
- `POST /pantry` — add an item (from search result or manual entry),
  with `tracking_mode`, initial `remaining_servings` (if countable),
  `expiration_date`.
- `PATCH /pantry/{id}` — edit quantity/expiration/mode directly (e.g. user
  manually corrects remaining count without going through consume).
- `POST /pantry/{id}/consume` — the atomic pantry-to-diary action above.
- `POST /pantry/{id}/finish` — mark a bulk item as used up (no more
  consume actions expected, e.g. "I threw out the empty spice jar").
- `DELETE /pantry/{id}` — remove without logging to diary (e.g. expired,
  thrown away, bought by mistake).
- `GET /pantry/expiring?days=N` — items expiring soon, for a dashboard/
  notification surface later.

## Progress log / handoff state (2026-07-19, context nearing limit)

**Done, verified against real infrastructure:**
- Full schema (diary + targets + water + notes + pantry) written to
  `app/backend/app/db.py`'s `init_db()`. Ran successfully against the real
  Neon `nutrition` database — confirmed via direct query that all 12
  tables exist: `credentials, daily_nutrition, diary_notes, food_log,
  food_log_nutrients, lift_orm, macro_target_settings, nutrition_targets,
  pantry_items, user_profile, users, water_log`.
- `app/backend/dri_reference.py` — DRI reference data (25 nutrients,
  adult sex/age brackets), sourced and cited from NIH Bookshelf
  NBK222881 (RDA/AI) and NBK278991 (UL). Spot-checked ~15 values directly
  against the fetched source tables during this session — all matched.
  `get_targets_for(sex, age)` tested and returns correct values (verified
  Iron sex-difference: 18mg premenopausal female vs 8mg male/
  postmenopausal female — the one nutrient most likely to be transcribed
  backwards, explicitly checked).
- Deliberately excluded from DRI table: pediatric/infant brackets (app has
  no pediatric users), pregnancy/lactation (real Cronometer category, not
  built — flagged, not silently approximated).

**NOT yet done — this is the actual next-session starting point:**
- No Python code written yet for: food_log_nutrients population (task 3),
  targets settings API (task 4), water tracking API (task 5), diary notes
  API (task 6), pantry API (all the routes listed in the Pantry section
  above — none exist as code yet, only as schema + design), account setup
  fields in Settings (task 7), any frontend (tasks 8-10).
- `routers/food.py` still writes old-shape entries only (calories/protein/
  carbs/fat/fiber columns) — does NOT yet insert into `food_log_nutrients`.
  This is the very next code change needed.
- No macro-gram derivation function written yet for
  `macro_target_settings.mode = 'ratio'` (4 kcal/g protein+carbs, 9 kcal/g
  fat — standard, uncontroversial, just not coded).

**Explicit user requirement to hold throughout remaining work (see
"API-first constraint" section above, and steering message during this
session): every capability must be a real, independently-callable REST
endpoint, not frontend-embedded logic.** This includes: progress-vs-target
computation (build as a GET endpoint returning computed percentages, don't
make the frontend fetch raw numbers and compute), macro gram derivation
from ratios, DRI resolution, pantry-consume's atomic food_log+decrement
logic — all belong in backend routes/services, callable via bare
`curl`+Bearer token, matching every other endpoint in this app.

**Suggested immediate next steps, in order:**
1. Update `app/backend/app/user_db.py` (or a new
   `app/backend/app/nutrition_targets.py` service module, following the
   existing file-per-concern pattern in this codebase) with:
   `seed_dri_targets(user_id, sex, age)` — calls `get_targets_for`, upserts
   into `nutrition_targets` with `is_custom=false` for any row not already
   custom (the "don't clobber explicit overrides" behavior already
   designed above).
2. Extend `routers/food.py`'s `/food/log` POST to also insert into
   `food_log_nutrients` from the `nutrients` dict already accepted in the
   request body (see existing docstring in that file — the field exists
   in the request shape today, just isn't persisted structurally).
3. New `routers/targets.py`: `GET/PUT /targets/macros`,
   `GET/PUT /targets/nutrients` (list + per-nutrient override),
   `GET /targets/progress?date=` (resolved target vs. actual, the core
   dashboard-progress-bar data source).
4. New `routers/water.py`, `routers/notes.py`, `routers/pantry.py` per the
   API surfaces already specified above.
5. Account setup: add age/sex/height/weight/activity fields somewhere
   reachable pre-first-use (Settings page today only has Cronometer/Hevy
   credentials — confirmed via direct file read this session, no profile
   fields exist anywhere yet). Wire save -> call `seed_dri_targets` +
   compute sex-based `water_target_ml` default (48 fl oz / ~1420 mL female,
   64 fl oz / ~1890 mL male, per Cronometer's cited default — convert to mL
   for internal storage per the schema's `water_target_ml` column, matching
   the "store mL internally, display-unit is a UI preference only"
   decision already made above).
6. Frontend last, per original task ordering — backend fully working and
   curl-testable first.

## Open decisions needing your call before I start building

1. **Nutrition Scores / Nutrient Balance gauges**: DEFERRED (confirmed).
2. **Water tracking**: IN SCOPE (confirmed). Storing units preference
   (cups/oz/mL) is a small UI nicety on top of storing raw mL — will
   default to mL internally regardless of what the user sees, so the
   schema doesn't need a units column, just a display preference in
   settings.
3. **Fasting**: DEFERRED (confirmed) — timestamp handling unvalidated.
4. **Notes with photos**: DEFERRED (confirmed) — image storage/cost
   undecided. Plain-text notes without attachments are cheap and could
   still ship in this phase if useful — will include a minimal
   `diary_notes` table (`user_id, date, text`) unless you'd rather cut it
   entirely too; flagging as a small addition, not asking you to
   re-decide the whole thing.
5. **Account setup fields**: CONFIRMED — will collect age, sex,
   height/weight, and activity level for DRI life-stage lookup and the sex-
   based water goal default. Correction to an earlier claim in this doc:
   checked and none of these fields exist anywhere today — Settings only
   has Cronometer/Hevy credential fields, and `tdee.py`'s BMR calculation
   is derived purely from logged weight-change + calorie regression, not
   from an age/sex formula. This is new profile data, not a duplicate of
   something existing.
6. **Notes (text now, photos later)**: CONFIRMED — plain-text notes ship
   in this phase. Schema built forward-compatible with a future photo
   attachment without a migration: `diary_notes` gets an `attachment_url`
   column (nullable, unused for now) rather than bolting on a separate
   table later. When photos are built, the actual files go to an
   unstructured/object store (GCS — already provisioned as
   `trackstack-storage` for this exact purpose per the GCP migration
   notes, `wardrobe/{user_id}/` and `nutrition/{user_id}/` prefixes were
   already sketched there), with this column holding the object key/URL —
   Postgres stores structured metadata + the pointer, GCS stores the
   blob. Not building the upload path now, just not painting ourselves
   into a schema change to add it.

Given the size, build order once you sign off: (a)
`food_log_nutrients` schema + backend nutrient-complete logging, (b)
targets tables + DRI seed data + targets settings API + account-setup
fields, (c) diary frontend page (food logging + water widget), (d)
dashboard progress-bar section wired to (b)+(c).

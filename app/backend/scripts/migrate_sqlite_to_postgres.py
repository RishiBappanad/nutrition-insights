"""
One-time data migration: copy existing per-user SQLite data into the shared
Postgres database.

Migrates:
  - app_data/nutrition_insights_app.db  -> users, credentials tables
  - app_data/user_{id}/data.db          -> daily_nutrition, lift_orm, food_log
                                            (scoped by user_id column)

Usage:
    cd app/backend
    python scripts/migrate_sqlite_to_postgres.py [--dry-run]

Safe to re-run: uses ON CONFLICT upserts for daily_nutrition/lift_orm/credentials,
and skips users/food_log rows that already exist (matched by username / by
user_id+date+food_name+source_id for food_log, since food_log has no natural key).
"""
import argparse
import asyncio
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# Make `app` importable when run as a script from app/backend/
BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.db import get_pool, init_db, close_db  # noqa: E402

APP_DB_PATH = BACKEND_ROOT / "app_data" / "nutrition_insights_app.db"
APP_DATA_DIR = BACKEND_ROOT / "app_data"


def _sqlite_conn(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _parse_sqlite_timestamp(value):
    """SQLite stores created_at as a plain string; asyncpg needs a datetime for TIMESTAMPTZ."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return datetime.utcnow()  # fallback: shouldn't happen, but don't fail the migration over it


async def migrate_users_and_credentials(pool, dry_run: bool) -> dict:
    """Migrate app-level users/credentials. Returns {old_user_id: new_user_id}
    mapping (old == new here since we preserve ids via explicit INSERT)."""
    if not APP_DB_PATH.exists():
        print(f"  No app-level db found at {APP_DB_PATH}, skipping users/credentials")
        return {}

    conn = _sqlite_conn(APP_DB_PATH)
    users = conn.execute("SELECT * FROM users").fetchall()
    creds = conn.execute("SELECT * FROM credentials").fetchall()
    conn.close()

    id_map = {}
    async with pool.acquire() as pg:
        for u in users:
            print(f"  users: {u['username']} (id={u['id']})")
            if dry_run:
                id_map[u["id"]] = u["id"]
                continue
            # Preserve the original id so per-user data (app_data/user_{id}) lines up
            await pg.execute(
                """INSERT INTO users (id, username, password_hash, created_at)
                   VALUES ($1, $2, $3, $4)
                   ON CONFLICT (id) DO NOTHING""",
                u["id"], u["username"], u["password_hash"], _parse_sqlite_timestamp(u["created_at"]),
            )
            id_map[u["id"]] = u["id"]

        for c in creds:
            print(f"  credentials: user_id={c['user_id']}")
            if dry_run:
                continue
            await pg.execute(
                """INSERT INTO credentials (user_id, hevy_username, hevy_password,
                       cronometer_username, cronometer_password)
                   VALUES ($1, $2, $3, $4, $5)
                   ON CONFLICT (user_id) DO NOTHING""",
                c["user_id"], c["hevy_username"], c["hevy_password"],
                c["cronometer_username"], c["cronometer_password"],
            )

    # Bump the users id sequence past the max migrated id so future inserts don't collide
    if not dry_run and users:
        max_id = max(u["id"] for u in users)
        async with pool.acquire() as pg:
            await pg.execute(
                "SELECT setval(pg_get_serial_sequence('users', 'id'), $1)", max_id
            )

    return id_map


async def migrate_user_data(pool, user_id: int, dry_run: bool, known_user_ids: set):
    """Migrate one user's data.db (daily_nutrition, lift_orm, food_log)."""
    if user_id not in known_user_ids:
        print(f"  user_{user_id}: SKIPPED — no matching row in users table "
              f"(orphaned app_data directory, not a real account)")
        return

    db_path = APP_DATA_DIR / f"user_{user_id}" / "data.db"
    if not db_path.exists():
        print(f"  user_{user_id}: no data.db found, skipping")
        return

    conn = _sqlite_conn(db_path)

    nutrition_rows = conn.execute("SELECT date, metric, value FROM daily_nutrition").fetchall()
    orm_rows = conn.execute("SELECT date, exercise, orm FROM lift_orm").fetchall()
    try:
        food_rows = conn.execute("SELECT * FROM food_log").fetchall()
    except sqlite3.OperationalError:
        food_rows = []  # table may not exist on older per-user dbs
    conn.close()

    print(f"  user_{user_id}: {len(nutrition_rows)} nutrition rows, "
          f"{len(orm_rows)} orm rows, {len(food_rows)} food_log rows")

    if dry_run:
        return

    async with pool.acquire() as pg:
        if nutrition_rows:
            await pg.executemany(
                """INSERT INTO daily_nutrition (user_id, date, metric, value)
                   VALUES ($1, $2, $3, $4)
                   ON CONFLICT (user_id, date, metric) DO UPDATE SET value = EXCLUDED.value""",
                [(user_id, r["date"], r["metric"], r["value"]) for r in nutrition_rows],
            )

        if orm_rows:
            await pg.executemany(
                """INSERT INTO lift_orm (user_id, date, exercise, orm)
                   VALUES ($1, $2, $3, $4)
                   ON CONFLICT (user_id, date, exercise) DO UPDATE SET orm = EXCLUDED.orm""",
                [(user_id, r["date"], r["exercise"], r["orm"]) for r in orm_rows],
            )

        for r in food_rows:
            # No natural unique key on food_log; skip if an identical-looking row
            # already exists for this user to keep re-runs idempotent.
            existing = await pg.fetchval(
                """SELECT 1 FROM food_log
                   WHERE user_id = $1 AND date = $2 AND food_name = $3
                     AND COALESCE(source_id, '') = COALESCE($4, '')
                   LIMIT 1""",
                user_id, r["date"], r["food_name"], r["source_id"],
            )
            if existing:
                continue
            await pg.execute(
                """INSERT INTO food_log (user_id, date, meal, food_name, source, source_id,
                       serving_size, serving_unit, calories, protein, carbs, fat, fiber, nutrients_json)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)""",
                user_id, r["date"], r["meal"], r["food_name"], r["source"], r["source_id"],
                r["serving_size"], r["serving_unit"], r["calories"], r["protein"],
                r["carbs"], r["fat"], r["fiber"], r["nutrients_json"],
            )


def discover_user_ids() -> list:
    """Find numeric user_{id} directories under app_data/ (skips user_test, test_export, etc.)."""
    ids = []
    for d in sorted(APP_DATA_DIR.iterdir()):
        if d.is_dir() and d.name.startswith("user_"):
            suffix = d.name[len("user_"):]
            if suffix.isdigit():
                ids.append(int(suffix))
    return sorted(ids)


async def main(dry_run: bool):
    print(f"{'[DRY RUN] ' if dry_run else ''}Migrating SQLite data -> Postgres")
    print(f"Source: {APP_DATA_DIR}")

    if not dry_run:
        await init_db()

    pool = await get_pool()

    print("\n== App-level users/credentials ==")
    id_map = await migrate_users_and_credentials(pool, dry_run)
    known_user_ids = set(id_map.keys())

    user_ids = discover_user_ids()
    print(f"\n== Per-user data ({len(user_ids)} candidate dirs: {user_ids}) ==")
    for uid in user_ids:
        await migrate_user_data(pool, uid, dry_run, known_user_ids)

    await close_db()
    print("\nDone." if not dry_run else "\nDry run complete — no data was written.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print what would be migrated without writing")
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run))

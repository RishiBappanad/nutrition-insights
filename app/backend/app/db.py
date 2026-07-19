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
    """Get (or lazily create) the shared connection pool."""
    global _pool
    if _pool is None:
        if not DATABASE_URL:
            raise RuntimeError("DATABASE_URL must be set")
        _pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10)
    return _pool


async def init_db():
    """Create app-level tables (users, credentials) plus user-scoped data
    tables (daily_nutrition, lift_orm, food_log) if they don't already exist.
    All data tables are scoped by user_id since Postgres is shared across users."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT now()
            );

            CREATE TABLE IF NOT EXISTS credentials (
                user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                hevy_username TEXT,
                hevy_password TEXT,
                cronometer_username TEXT,
                cronometer_password TEXT
            );

            CREATE TABLE IF NOT EXISTS daily_nutrition (
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                date TEXT NOT NULL,
                metric TEXT NOT NULL,
                value DOUBLE PRECISION NOT NULL,
                PRIMARY KEY (user_id, date, metric)
            );

            CREATE TABLE IF NOT EXISTS lift_orm (
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                date TEXT NOT NULL,
                exercise TEXT NOT NULL,
                orm DOUBLE PRECISION NOT NULL,
                PRIMARY KEY (user_id, date, exercise)
            );

            CREATE TABLE IF NOT EXISTS food_log (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
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

            CREATE INDEX IF NOT EXISTS idx_nutrition_metric ON daily_nutrition(user_id, metric, date);
            CREATE INDEX IF NOT EXISTS idx_orm_exercise ON lift_orm(user_id, exercise, date);
            CREATE INDEX IF NOT EXISTS idx_food_log_date ON food_log(user_id, date);
        """)


async def close_db():
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None

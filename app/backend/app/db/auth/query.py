from .. import get_pool


async def ensure_local_user(account_id: int, email: str) -> None:
    """Create a mirror row in the local users table if one doesn't exist
    yet. Safe to call on every request -- INSERT ... ON CONFLICT DO
    NOTHING."""
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            """INSERT INTO users (id, username, password_hash)
               VALUES ($1, $2, 'trackstack-auth')
               ON CONFLICT (id) DO NOTHING""",
            account_id, email,
        )
        await db.execute(
            "INSERT INTO credentials (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING",
            account_id,
        )


async def save_credentials(user_id: int, cronometer_username, cronometer_password) -> None:
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            """UPDATE credentials SET
                cronometer_username = $1, cronometer_password = $2
            WHERE user_id = $3""",
            cronometer_username, cronometer_password, user_id,
        )

"""
Auth for nutrition-insights.

Identity (registration, login, Google OAuth) is owned by trackstack-auth,
not this service. This module only:
  1. Verifies JWTs issued by trackstack-auth (same shared JWT_SECRET, so no
     network call to trackstack-auth is needed per request).
  2. Ensures a local `users` row exists for the account (id = account_id),
     created lazily on first authenticated request — nutrition still needs
     a local users.id to satisfy existing foreign keys on credentials,
     daily_nutrition, lift_orm, and food_log, but it's a mirror, not the
     source of truth.
  3. Owns nutrition-specific data unrelated to identity, like Hevy/Cronometer
     credentials (see /credentials below).
"""
import os
from fastapi import APIRouter, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from pydantic import BaseModel

from ..db import get_pool, encrypt, decrypt

router = APIRouter()
security = HTTPBearer()

SECRET_KEY = os.getenv("JWT_SECRET", "change-me-in-production")
ALGORITHM = "HS256"


class CredentialsRequest(BaseModel):
    cronometer_username: str = ""
    cronometer_password: str = ""


async def _ensure_local_user(account_id: int, email: str) -> None:
    """Create a mirror row in the local users table if one doesn't exist yet.
    Safe to call on every request — INSERT ... ON CONFLICT DO NOTHING."""
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


async def get_current_user(creds: HTTPAuthorizationCredentials = Depends(security)) -> int:
    """Verify a trackstack-auth JWT and return the account id.
    Ensures a local mirror row exists so downstream FK-dependent queries work."""
    try:
        payload = jwt.decode(creds.credentials, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

    account_id = payload.get("accountId")
    email = payload.get("email")
    if account_id is None or email is None:
        raise HTTPException(status_code=401, detail="Invalid token")

    await _ensure_local_user(account_id, email)
    return account_id


@router.post("/credentials")
async def save_credentials(req: CredentialsRequest, user_id: int = Depends(get_current_user)):
    """hevy_username/hevy_password columns still exist on `credentials`
    but are no longer written here -- Hevy sync was removed 2026-09-05
    (see archive/hevy_fitness_tracker/ in the backend). Any value a user
    saved previously is left untouched, not wiped, in case it's useful
    when porting to a future fitness tracker."""
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            """UPDATE credentials SET
                cronometer_username = $1, cronometer_password = $2
            WHERE user_id = $3""",
            encrypt(req.cronometer_username) if req.cronometer_username else None,
            encrypt(req.cronometer_password) if req.cronometer_password else None,
            user_id,
        )
    return {"status": "saved"}

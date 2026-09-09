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

from ..db import encrypt, decrypt
from ..db.auth import query as auth_query

router = APIRouter()
security = HTTPBearer()

SECRET_KEY = os.environ["JWT_SECRET"]
# Was os.getenv("JWT_SECRET", "change-me-in-production") -- a silent
# fallback that let this service run with a guessable, publicly-visible
# secret if the real one ever failed to load, instead of refusing to
# start. This is exactly the failure mode that let a real JWT_SECRET
# mismatch between this service and trackstack-auth (the token issuer)
# go unnoticed: every token trackstack-auth issued was silently rejected
# here, but the service itself looked healthy the whole time. Failing
# loudly at import time (KeyError, not a fallback) matches how
# trackstack-auth itself already handles this.
ALGORITHM = "HS256"


class CredentialsRequest(BaseModel):
    cronometer_username: str = ""
    cronometer_password: str = ""


async def _ensure_local_user(account_id: int, email: str) -> None:
    await auth_query.ensure_local_user(account_id, email)


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
    await auth_query.save_credentials(
        user_id,
        encrypt(req.cronometer_username) if req.cronometer_username else None,
        encrypt(req.cronometer_password) if req.cronometer_password else None,
    )
    return {"status": "saved"}

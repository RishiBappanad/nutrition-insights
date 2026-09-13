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
import asyncio
import os
from typing import Optional
import requests
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


# trackstack-auth is the only place personal-access-token storage/lookup
# lives -- a token this service's own JWT verification doesn't recognize is
# checked against trackstack-auth's POST /tokens/verify instead, mirroring
# todo-tracker's requireAuth (the first tracker to actually implement this).
# Despite ACTIONS_CONTRACT_SPEC.md documenting PATs as working "everywhere
# requireAuth is used," this service never actually had the fallback --
# confirmed live: a real PAT returned 401 here while working fine against
# todo-tracker. Uses `requests` (this codebase's only HTTP client) inside
# asyncio.to_thread rather than calling it directly -- a bare blocking call
# in an async function is exactly the class of bug already found and fixed
# once in this codebase's Cronometer sync (see sync.py's history), where a
# blocking call inside an async def froze the entire event loop, not just
# the one request.
TRACKSTACK_AUTH_URL = os.environ.get("TRACKSTACK_AUTH_URL")


def _verify_personal_access_token_sync(token: str) -> Optional[dict]:
    if not TRACKSTACK_AUTH_URL:
        return None
    try:
        res = requests.post(f"{TRACKSTACK_AUTH_URL}/tokens/verify", json={"token": token}, timeout=5)
        if not res.ok:
            return None
        return res.json().get("account")
    except requests.RequestException:
        return None


async def get_current_user(creds: HTTPAuthorizationCredentials = Depends(security)) -> int:
    """Verify a trackstack-auth JWT (or, failing that, a personal access
    token) and return the account id. Ensures a local mirror row exists so
    downstream FK-dependent queries work."""
    account_id = None
    email = None
    try:
        payload = jwt.decode(creds.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        account_id = payload.get("accountId")
        email = payload.get("email")
    except JWTError:
        pass  # Not a valid JWT -- fall through and try it as a PAT below.

    if account_id is None or email is None:
        account = await asyncio.to_thread(_verify_personal_access_token_sync, creds.credentials)
        if account:
            account_id = account.get("accountId")
            email = account.get("email")

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

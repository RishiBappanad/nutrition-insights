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
from pydantic import BaseModel
from trackstack_auth_client import verify_trackstack_token

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
TRACKSTACK_AUTH_URL = os.environ.get("TRACKSTACK_AUTH_URL")


class CredentialsRequest(BaseModel):
    cronometer_username: str = ""
    cronometer_password: str = ""


async def _ensure_local_user(account_id: int, email: str) -> None:
    await auth_query.ensure_local_user(account_id, email)


async def get_current_user(creds: HTTPAuthorizationCredentials = Depends(security)) -> int:
    """Verify a trackstack-auth JWT (or, failing that, a personal access
    token) and return the account id. Ensures a local mirror row exists so
    downstream FK-dependent queries work.

    JWT-then-PAT verification is the shared, contract-tested
    trackstack-auth-client package -- this service's own hand-copy of it
    (added a few commits ago after finding it was silently missing the
    PAT fallback entirely) is now just a call into that."""
    account = await verify_trackstack_token(creds.credentials, SECRET_KEY, TRACKSTACK_AUTH_URL)
    if not account:
        raise HTTPException(status_code=401, detail="Invalid token")

    await _ensure_local_user(account["accountId"], account["email"])
    return account["accountId"]


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

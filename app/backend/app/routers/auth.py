import os
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from passlib.context import CryptContext
from pydantic import BaseModel
import requests

from ..db import get_pool, encrypt, decrypt

router = APIRouter()
security = HTTPBearer()
pwd_context = CryptContext(schemes=["bcrypt"])

SECRET_KEY = os.getenv("JWT_SECRET", "change-me-in-production")
ALGORITHM = "HS256"
TOKEN_EXPIRE_DAYS = 30

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "https://trackstack-proxy-production.up.railway.app/nutrition/auth/google/callback")


class RegisterRequest(BaseModel):
    username: str
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


class CredentialsRequest(BaseModel):
    hevy_username: str = ""
    hevy_password: str = ""
    cronometer_username: str = ""
    cronometer_password: str = ""


def create_token(user_id: int) -> str:
    expire = datetime.utcnow() + timedelta(days=TOKEN_EXPIRE_DAYS)
    return jwt.encode({"sub": str(user_id), "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)


async def get_current_user(creds: HTTPAuthorizationCredentials = Depends(security)) -> int:
    try:
        payload = jwt.decode(creds.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        return int(payload["sub"])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


@router.post("/register")
async def register(req: RegisterRequest):
    pool = await get_pool()
    async with pool.acquire() as db:
        existing = await db.fetchrow("SELECT id FROM users WHERE username = $1", req.username)
        if existing:
            raise HTTPException(status_code=400, detail="Username taken")

        hashed = pwd_context.hash(req.password)
        user_id = await db.fetchval(
            "INSERT INTO users (username, password_hash) VALUES ($1, $2) RETURNING id",
            req.username, hashed,
        )
        await db.execute("INSERT INTO credentials (user_id) VALUES ($1)", user_id)
    return {"token": create_token(user_id)}


@router.post("/login")
async def login(req: LoginRequest):
    pool = await get_pool()
    async with pool.acquire() as db:
        user = await db.fetchrow(
            "SELECT id, password_hash FROM users WHERE username = $1", req.username
        )

    if not user or not pwd_context.verify(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    return {"token": create_token(user["id"])}


@router.post("/credentials")
async def save_credentials(req: CredentialsRequest, user_id: int = Depends(get_current_user)):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            """UPDATE credentials SET
                hevy_username = $1, hevy_password = $2,
                cronometer_username = $3, cronometer_password = $4
            WHERE user_id = $5""",
            encrypt(req.hevy_username) if req.hevy_username else None,
            encrypt(req.hevy_password) if req.hevy_password else None,
            encrypt(req.cronometer_username) if req.cronometer_username else None,
            encrypt(req.cronometer_password) if req.cronometer_password else None,
            user_id,
        )
    return {"status": "saved"}


@router.get("/google")
async def google_login():
    """Redirect URL for Google OAuth login."""
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=500, detail="Google OAuth not configured")
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
    }
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return {"url": f"https://accounts.google.com/o/oauth2/v2/auth?{query}"}


@router.get("/google/callback")
async def google_callback(code: str):
    """Exchange Google auth code for a JWT token."""
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise HTTPException(status_code=500, detail="Google OAuth not configured")

    # Exchange code for tokens
    token_resp = requests.post("https://oauth2.googleapis.com/token", data={
        "code": code,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "grant_type": "authorization_code",
    })
    if not token_resp.ok:
        raise HTTPException(status_code=400, detail="Failed to exchange Google code")

    access_token = token_resp.json().get("access_token")

    # Fetch Google user info
    user_info = requests.get("https://www.googleapis.com/oauth2/v2/userinfo",
        headers={"Authorization": f"Bearer {access_token}"}
    ).json()

    email = user_info.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="Could not get email from Google")

    # Find or create user
    pool = await get_pool()
    async with pool.acquire() as db:
        user = await db.fetchrow("SELECT id FROM users WHERE username = $1", email)

        if not user:
            # Create new user with Google email as username (no password)
            user_id = await db.fetchval(
                "INSERT INTO users (username, password_hash) VALUES ($1, $2) RETURNING id",
                email, "google-oauth",
            )
            await db.execute("INSERT INTO credentials (user_id) VALUES ($1)", user_id)
        else:
            user_id = user["id"]

    token = create_token(user_id)

    # Redirect to app with token in URL fragment (client handles it).
    # FRONTEND_BASE_PATH defaults to "/" for standalone deploys (Cloud Run);
    # set to "/nutrition/" when running behind the proxy.
    from fastapi.responses import RedirectResponse
    base_path = os.getenv("FRONTEND_BASE_PATH", "/")
    return RedirectResponse(url=f"{base_path}#google_token={token}")

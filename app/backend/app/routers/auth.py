import os
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from passlib.context import CryptContext
from pydantic import BaseModel

from ..db import get_db, encrypt, decrypt

router = APIRouter()
security = HTTPBearer()
pwd_context = CryptContext(schemes=["bcrypt"])

SECRET_KEY = os.getenv("JWT_SECRET", "change-me-in-production")
ALGORITHM = "HS256"
TOKEN_EXPIRE_DAYS = 30


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
    db = await get_db()
    existing = await db.execute("SELECT id FROM users WHERE username = ?", (req.username,))
    if await existing.fetchone():
        await db.close()
        raise HTTPException(status_code=400, detail="Username taken")

    hashed = pwd_context.hash(req.password)
    cursor = await db.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", (req.username, hashed))
    user_id = cursor.lastrowid
    await db.execute("INSERT INTO credentials (user_id) VALUES (?)", (user_id,))
    await db.commit()
    await db.close()
    return {"token": create_token(user_id)}


@router.post("/login")
async def login(req: LoginRequest):
    db = await get_db()
    row = await db.execute("SELECT id, password_hash FROM users WHERE username = ?", (req.username,))
    user = await row.fetchone()
    await db.close()

    if not user or not pwd_context.verify(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    return {"token": create_token(user["id"])}


@router.post("/credentials")
async def save_credentials(req: CredentialsRequest, user_id: int = Depends(get_current_user)):
    db = await get_db()
    await db.execute(
        """UPDATE credentials SET
            hevy_username = ?, hevy_password = ?,
            cronometer_username = ?, cronometer_password = ?
        WHERE user_id = ?""",
        (
            encrypt(req.hevy_username) if req.hevy_username else None,
            encrypt(req.hevy_password) if req.hevy_password else None,
            encrypt(req.cronometer_username) if req.cronometer_username else None,
            encrypt(req.cronometer_password) if req.cronometer_password else None,
            user_id,
        ),
    )
    await db.commit()
    await db.close()
    return {"status": "saved"}

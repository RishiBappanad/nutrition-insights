"""Plain-text diary notes API, scoped by user+date. One note per day —
saving overwrites, matching the schema's PRIMARY KEY (user_id, date).

attachment_url exists in the schema but is intentionally not exposed as a
settable field here — photo attachments are deferred (see design doc),
this endpoint only ever writes it as NULL. Read-side still returns it so
the response shape doesn't need to change when photo upload is built."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..routers.auth import get_current_user
from ..db import get_pool

router = APIRouter()


class NoteRequest(BaseModel):
    date: str
    text: str


@router.put("/")
async def set_note(req: NoteRequest, user_id: int = Depends(get_current_user)):
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="text must not be empty — use DELETE to remove a note")
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO diary_notes (user_id, date, text, updated_at)
               VALUES ($1, $2, $3, now())
               ON CONFLICT (user_id, date) DO UPDATE SET text = EXCLUDED.text, updated_at = now()""",
            user_id, req.date, req.text,
        )
    return {"status": "saved"}


@router.get("/")
async def get_note(date: str = Query(...), user_id: int = Depends(get_current_user)):
    """Returns null fields (not a 404) when no note exists for the date —
    'no note yet' is a normal, expected state for a diary day, not an
    error condition."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT text, attachment_url, updated_at FROM diary_notes WHERE user_id = $1 AND date = $2",
            user_id, date,
        )
    if row is None:
        return {"date": date, "text": None, "attachment_url": None, "updated_at": None}
    return {
        "date": date,
        "text": row["text"],
        "attachment_url": row["attachment_url"],
        "updated_at": row["updated_at"].isoformat(),
    }


@router.delete("/")
async def delete_note(date: str = Query(...), user_id: int = Depends(get_current_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM diary_notes WHERE user_id = $1 AND date = $2", user_id, date
        )
    return {"status": "deleted"}

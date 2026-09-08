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
from ..db.notes import query as notes_query

router = APIRouter()


class NoteRequest(BaseModel):
    date: str
    text: str


@router.put("/")
async def set_note(req: NoteRequest, user_id: int = Depends(get_current_user)):
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="text must not be empty — use DELETE to remove a note")
    await notes_query.upsert_note(user_id, req.date, req.text)
    return {"status": "saved"}


@router.get("/")
async def get_note(date: str = Query(...), user_id: int = Depends(get_current_user)):
    """Returns null fields (not a 404) when no note exists for the date —
    'no note yet' is a normal, expected state for a diary day, not an
    error condition."""
    row = await notes_query.get_note(user_id, date)
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
    await notes_query.delete_note(user_id, date)
    return {"status": "deleted"}

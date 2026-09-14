"""
Universal Event Contract backing store -- see db/__init__.py's
domain_events table comment for the full design rationale (one
polymorphic table + one shared helper per tracker, generalizing
todo-tracker's todo_events pattern across every domain entity this
tracker owns, instead of GET /events deriving events live from each
table's current rows).

Every mutating route (create/update/delete) for an entity that should
appear in the Event Contract calls log_domain_event() as part of the
SAME transaction as its actual write -- pass the already-open `conn`
so a rolled-back mutation can never leave an orphaned event behind.
"""
import json
from datetime import date
from typing import Optional


async def log_domain_event(
    conn,
    user_id: int,
    owner_type: str,
    owner_id: int,
    action: str,
    category: Optional[str] = None,
    amount: float = 0,
    label: Optional[str] = None,
    source: Optional[str] = None,
    source_id: Optional[str] = None,
    metadata: Optional[dict] = None,
    occurred_at: Optional[str] = None,
) -> None:
    """`action` is "created" | "updated" | "deleted" -- combined with
    `owner_type` into the stored event_type ("food_log_created", etc.),
    matching the Core Event Shape's existing per-tracker event_type
    convention rather than introducing a separate generic action field.

    `occurred_at` is the entity's OWN business date if it has one (e.g.
    food_log.date, which a user can backdate to "yesterday I forgot to
    log lunch") -- pass it explicitly whenever the entity has a
    meaningful date of its own; leave it None only for entities with no
    such concept (pantry items, todos), where "when this happened" is
    genuinely just "when this action was taken," and the column's
    DEFAULT now() is correct. Getting this wrong would silently break
    the Core Event Shape's own definition of occurred_at ("when the
    thing actually happened, not when the row was written").

    Every business date in this app is stored as plain "YYYY-MM-DD" TEXT
    (see db/__init__.py's convention), never a native date/timestamp
    column -- occurred_at is parsed into a real date object here because
    asyncpg's timestamptz codec requires an actual date/datetime
    instance, not a string, once the column resolves to timestamptz."""
    occurred_at_value = date.fromisoformat(occurred_at) if occurred_at else None
    await conn.execute(
        """INSERT INTO domain_events
               (user_id, owner_type, owner_id, event_type, category, amount, label, source, source_id, metadata_json, occurred_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, COALESCE($11, now()))""",
        user_id, owner_type, owner_id, f"{owner_type}_{action}", category, amount, label, source, source_id,
        json.dumps(metadata or {}), occurred_at_value,
    )

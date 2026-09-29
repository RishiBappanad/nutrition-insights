"""
trackstack-calendar-client -- shared calendar-push client for
TrackStack tracker backends (Python).

Lets a tracker push a calendar-worthy entry to trackstack-gateway's
POST /api/calendar/entries at the moment it decides something is
calendar-worthy, right next to its own log_domain_event() call -- see
workspace-notes/CALENDAR_INTEGRATION_SPEC.md. The returned pusher
NEVER raises: a calendar push is fire-and-forget by design (see that
spec's "Failure Handling" section) -- losing one push means one
stale/missing calendar entry, an acceptable, self-correcting cost next
time the same entity is pushed again, not something worth retry-queue
complexity or risking the caller's own request over.

Behavior here must match trackstack-ui's Node "/calendar-client"
subpath exactly -- see CALENDAR_CONTRACT_FIXTURE.json at the repo
root, which both implementations' test suites are run against.
"""
import asyncio
from typing import Any, Dict, Optional

import requests


def _build_request_body(tracker: str, **entry: Any) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "tracker": tracker,
        "owner_type": entry["owner_type"],
        "owner_id": entry["owner_id"],
        "event_type": entry["event_type"],
        "action": entry["action"],
        "occurred_at": entry["occurred_at"],
        "category": entry.get("category"),
        "amount": entry.get("amount"),
        "label": entry.get("label"),
        "color": entry.get("color"),
        "link": entry.get("link"),
        "metadata": entry.get("metadata") or {},
    }
    # Omitted entirely (not sent as null) when the caller doesn't pass
    # one, so the gateway's own "kind defaults to 'occurred'" behavior
    # applies.
    kind = entry.get("kind")
    if kind is not None:
        body["kind"] = kind
    return body


def _post_calendar_entry_sync(calendar_base_url: str, auth_header: str, body: Dict[str, Any], timeout: float) -> bool:
    try:
        res = requests.post(
            f"{calendar_base_url}/api/calendar/entries",
            json=body,
            headers={"Authorization": auth_header},
            timeout=timeout,
        )
        return res.ok
    except requests.RequestException as err:
        print(f"[calendar-client] push failed, continuing without it: {err}")
        return False


def create_calendar_pusher(calendar_base_url: str, tracker: str, timeout: float = 3.0):
    """Factory -- binds calendar_base_url/tracker once, the same
    ergonomic shape as trackstack-auth-client's own factories. The
    returned async function never raises; the actual HTTP call runs
    via asyncio.to_thread rather than a bare blocking `requests` call
    inside an async function -- a bare blocking call freezes the whole
    event loop, not just the one request, the exact class of bug
    already found and fixed once in nutrition-insights' own Cronometer
    sync."""

    async def push_calendar_entry(auth_header: str, **entry: Any) -> bool:
        body = _build_request_body(tracker, **entry)
        return await asyncio.to_thread(_post_calendar_entry_sync, calendar_base_url, auth_header, body, timeout)

    return push_calendar_entry

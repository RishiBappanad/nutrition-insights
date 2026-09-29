# trackstack-calendar-client (Python)

Lives here (inside nutrition-insights, its only real consumer today) rather
than as its own standalone repo -- it was never published to PyPI or given a
remote, and per Tenet #2 (don't build speculative infrastructure), a
dedicated shared-library repo isn't worth it for exactly one Python tracker.
If a second Python tracker ever needs this, extracting it back out into its
own repo (or a real PyPI publish) is a small, well-understood move -- same
shape as trackstack-ui's own `calendar-client` subpath, just for Node instead.

Shared calendar-push client for TrackStack tracker backends written in Python (FastAPI, etc.).

Lets a tracker push a calendar-worthy entry to [`trackstack-gateway`](https://github.com/RishiBappanad/trackstack-gateway)'s `POST /api/calendar/entries` at the moment it decides something is calendar-worthy, right next to its own domain-event logging call — see [`CALENDAR_INTEGRATION_SPEC.md`](https://github.com/RishiBappanad/workspace-notes/blob/main/CALENDAR_INTEGRATION_SPEC.md) for the full design. The gateway never reaches out to any tracker; a tracker pushes to it.

The Node/Express equivalent lives in [`trackstack-ui`](https://github.com/RishiBappanad/trackstack-ui)'s `trackstack-ui/calendar-client` subpath. Both implementations are tested against the same [`CALENDAR_CONTRACT_FIXTURE.json`](./CALENDAR_CONTRACT_FIXTURE.json) in this repo, so they can't silently drift apart.

The returned pusher **never raises**. A calendar push is fire-and-forget by design: losing one push means one stale/missing calendar entry, an acceptable, self-correcting cost next time the same entity is pushed again — not something worth retry-queue complexity or risking the caller's own request over.

## Install

Not published to PyPI (see above) -- install it locally, editable, from
nutrition-insights' own requirements.txt or directly:

```bash
pip install -e ./trackstack-calendar-client
```

## Usage

```python
from trackstack_calendar_client import create_calendar_pusher
import os

push_calendar_entry = create_calendar_pusher(
    os.environ["TRACKSTACK_GATEWAY_URL"],
    tracker="nutrition",
)

# Right next to an existing log_domain_event() call:
await push_calendar_entry(
    auth_header,               # the same "Bearer ..." header the triggering request arrived with
    owner_type="exercise_log",
    owner_id=str(exercise_log_id),
    event_type="exercise_log_created",
    action="created",          # 'created' | 'updated' | 'deleted'
    category="cardio",
    amount=300,
    label="Morning run",
    occurred_at=occurred_at.isoformat(),
    metadata={"duration_minutes": 30},
)
```

`kind` (`'occurred' | 'forecast' | 'goal'`, default `'occurred'`) can be passed as an extra keyword argument for a Recurring Item's predicted date or a Goal's threshold crossing — see `RECURRING_AND_GOALS_SPEC.md`.

## Development

```bash
pip install -e ".[dev]"
pytest
```

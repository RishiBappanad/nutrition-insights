# Hevy / strength-training integration (archived 2026-09-05)

Removed from nutrition-insights' live API per the project's tenet that
nutrition and fitness are separate trackers (CLAUDE.md's Seven Tenets,
#1/#3) — TrackStack itself should merge cross-tracker data, not have one
tracker's app own another domain's sync integration. Kept here, not
deleted, so it can be lifted into a future `trackstack-fitness` (or
similarly-named) service without redoing the GWT/web-scraping
reverse-engineering work.

## What's here

- `hevy_web.py` — Playwright-based web scraper that logs into Hevy and
  exports workout history to CSV. This is what `sync_routes.py` actually
  used in production.
- `hevy_rpc.py` — an earlier, unused attempt at a direct Hevy API/RPC
  client. Never wired into any route (confirmed via a full-codebase
  search before archiving) — kept alongside the web scraper only because
  it's Hevy-domain code that a future fitness tracker might resume or
  finish, not because it ever worked end-to-end here.
- `hevy_api_attempt.py` — a third, even older unused attempt (official
  Hevy API + key, `from src.config import settings` referencing a
  module layout this backend no longer has). Also never wired into any
  route. Kept only as a reference for whoever picks this up next; its
  import is already broken and not something a port should fix as-is.
- `sync_routes.py` — the FastAPI route code as it existed in
  `app/routers/sync.py` (the `POST /sync/hevy` endpoint) and
  `app/routers/data.py` (the CSV-file-based `/data/workouts` and
  `/data/orm` read endpoints) immediately before removal. Adapted only
  enough to note what it depended on from the rest of the app — not
  rewritten, so a port can start from working logic.

## What replaced this in nutrition-insights

Manual strength-training logging is now `POST /lifts/log`
(`app/routers/lifts.py`) — a deliberately minimal stand-in ("just a
dummy" per the decision that a real Hevy-equivalent belongs in its own
fitness tracker, not here): one weight x reps set in, one estimated 1RM
out, written straight to the same `lift_orm` table Hevy sync used to
populate. Charts' Exercise tab and Lift Insights keep working against
that same table, now fed manually instead of by sync.

## Porting checklist for the new app

1. `lift_orm` table (`user_id, date, exercise, orm`) — recreate its
   `CREATE TABLE IF NOT EXISTS` block from nutrition-insights'
   `app/db.py` (git history before this archive commit) if the new app
   wants to inherit any already-logged manual data; otherwise start
   fresh with its own schema.
2. `_compute_orm` (Brzycki/Epley 1RM estimation) now lives in
   `app/user_db.py` as `compute_orm()` in nutrition-insights — copy it
   over rather than re-deriving the formula.
3. Re-home `hevy_username`/`hevy_password` credential storage — these
   columns still exist, untouched, in nutrition-insights' `credentials`
   table for any user who saved them previously, but the new app should
   own its own credentials table per Tenet #1 (no cross-tracker shared
   DB access), not read nutrition-insights' database directly.
4. Wire the new app into TrackStack's auth/JWT contract and the
   Universal Event Contract adapter routes the same way nutrition-
   insights and finance-tracker already do (see CLAUDE.md's "How to Add
   a New Tracker").

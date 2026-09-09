import os

# JWT_SECRET is required (not optional) at import time by app/routers/auth.py
# -- fails loudly on purpose (2026-09-09, see that file's comment) instead of
# silently falling back to a guessable default. Most test files that care
# about a specific signing value already set this explicitly before
# importing the app (see test_events_adapter.py); this is just a safety-net
# default via setdefault() for tests that import the app without needing to
# sign a real token themselves, so a shared default doesn't override one a
# test file deliberately set. One place, not one line added per test file.
os.environ.setdefault("JWT_SECRET", "test-secret-for-unit-tests")

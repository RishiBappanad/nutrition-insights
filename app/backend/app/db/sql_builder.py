"""
Composable SQL string builders, plus the one sanitization choke point
every one of them routes through.

Every query.py module in this codebase writes raw SQL text -- that's
the point of the Named Query Modules pattern (see CLAUDE.md), not
something this file replaces. What this file replaces is each module
hand-assembling that text with its own f-strings: table names, column
lists, ORDER BY clauses, and RETURNING clauses were each written out
independently per call site, so the same "SELECT * FROM <table>" or
"RETURNING <cols>" shape got reinvented (and re-typo-able) everywhere.
These functions are that shape, named and given one home, matching the
same "modularize by target operation" ask that produced
db/__init__.py's delete_with_ownership_returning/insert_returning/
update_with_ownership_returning -- this is the general-purpose version
those three now build on top of, for the rest of this app's SQL
(SELECTs, dynamic WHERE clauses, ORDER BY) that those three don't cover.

Every identifier (table name, column name) passed to any function here
goes through validate_identifier() before it's interpolated into SQL
text. This is a defense-in-depth check, not the primary safety
mechanism -- the actual safety invariant, unchanged from before this
file existed, is that every real call site passes a hardcoded literal,
never a table/column name derived from request input. validate_identifier
exists so that if that invariant is ever accidentally broken (a typo, a
future call site that forgets it), the query fails loudly with a
ValueError instead of silently building an injectable string. It is NOT
a substitute for parameterized values -- every actual VALUE (not
identifier) in every function below still goes through asyncpg's own
$1/$2/... placeholders, never string interpolation.
"""
import re
from typing import Union

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def validate_identifier(name: str) -> str:
    """Raises ValueError if `name` isn't a plain SQL identifier (letters/
    digits/underscore, not starting with a digit) -- rejects anything
    that could break out of an identifier position (quotes, whitespace,
    semicolons, SQL keywords used as injection payloads, etc). Returns
    `name` unchanged so call sites can wrap an argument inline."""
    if not isinstance(name, str) or not _IDENTIFIER_RE.match(name):
        raise ValueError(f"unsafe SQL identifier: {name!r}")
    return name


def _validate_identifiers(names: list[str]) -> list[str]:
    return [validate_identifier(n) for n in names]


def select_clause(table: str, columns: Union[str, list[str]] = "*") -> str:
    """SELECT <columns> FROM <table>. `columns` may be the literal "*"
    or a list of column names (each validated); a caller needing a
    computed/aliased column list (e.g. "COUNT(*) AS total") passes it
    as the raw "*"-style string form, which is not validated as an
    identifier since it isn't one -- same trust boundary as `table`
    itself: only ever a hardcoded literal, never request-derived."""
    validate_identifier(table)
    column_sql = columns if isinstance(columns, str) else ", ".join(_validate_identifiers(columns))
    return f"SELECT {column_sql} FROM {table}"


def where_clause(conditions: list[str]) -> str:
    """conditions are already-built SQL boolean fragments (e.g.
    "user_id = $1", "is_finished = FALSE") -- not identifiers, so not
    passed through validate_identifier here. Every real condition's
    right-hand side is a $-placeholder or a hardcoded literal, never a
    request-derived value spliced into the fragment text itself."""
    return f" WHERE {' AND '.join(conditions)}" if conditions else ""


def order_by_clause(*columns: str) -> str:
    """Each entry may include a trailing direction ("food_name" or
    "food_name DESC") -- only the column part is validated as an
    identifier; ASC/DESC is checked against a fixed allow-list rather
    than accepted as free text."""
    parts = []
    for c in columns:
        tokens = c.split()
        validate_identifier(tokens[0])
        if len(tokens) > 1:
            direction = tokens[1].upper()
            if direction not in ("ASC", "DESC") or len(tokens) > 2:
                raise ValueError(f"unsafe ORDER BY clause: {c!r}")
            parts.append(f"{tokens[0]} {direction}")
        else:
            parts.append(tokens[0])
    return f" ORDER BY {', '.join(parts)}" if parts else ""


def insert_clause(table: str, columns: list[str]) -> str:
    """INSERT INTO <table> (<columns>) VALUES ($1, $2, ...) -- caller
    supplies the values themselves separately as bound parameters, in
    the same order as `columns`."""
    validate_identifier(table)
    validated = _validate_identifiers(columns)
    column_list = ", ".join(validated)
    placeholders = ", ".join(f"${i}" for i in range(1, len(validated) + 1))
    return f"INSERT INTO {table} ({column_list}) VALUES ({placeholders})"


def update_clause(table: str) -> str:
    validate_identifier(table)
    return f"UPDATE {table}"


def delete_clause(table: str) -> str:
    validate_identifier(table)
    return f"DELETE FROM {table}"


def set_clause(assignments: list[str]) -> str:
    """assignments are pre-built "<column> = <expr>" fragments (e.g. a
    COALESCE-guarded partial update) -- see
    update_with_ownership_returning, the one caller that needs the
    per-column COALESCE logic this can't generalize further without
    losing that "only overwrite what was actually provided" behavior."""
    return f" SET {', '.join(assignments)}"


def returning_clause(columns: list[str]) -> str:
    if not columns:
        return ""
    return f" RETURNING {', '.join(_validate_identifiers(columns))}"

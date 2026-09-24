"""
Goal Query interpreter -- Python/asyncpg port of finance-tracker's
lib/db/src/goal-query.ts, translating the JSONB measure_query/
reference_query shape (workspace-notes/RECURRING_AND_GOALS_SPEC.md's
"Goal Query -- the shared shape" section) into safe, parameterized SQL
against domain_events, and evaluating it.

This is the cross-tracker-standardized contract (field names, allowed
values, semantics) finance-tracker's own goal-query.ts already
implements -- see that spec section and that file before changing
anything here. Each tracker keeps its own copy of this interpreter
(Tenet #1), not shared code, the same way domain_events' shape is
standardized without a shared table.

Safety invariant (CLAUDE.md's "Composable, Sanitized SQL" pattern): every
aggregation/field/operator value from a GoalQuery is checked against a
strict allow-list (is_valid_*) before this module ever branches on it to
build a query. Filter VALUES always become real asyncpg $-placeholders,
never string-concatenated into query text -- `field`/`operator` decide
WHICH SQL fragment gets built, but the fragment's own parameter numbering
is what carries the untrusted value.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field
from datetime import date, datetime, timedelta, timezone
from typing import Optional, Union

from .db import get_pool

AGGREGATIONS = ("sum", "mean", "median", "min", "max", "count", "percentile")
FILTER_OPERATORS = ("eq", "ne", "gt", "gte", "lt", "lte", "in", "contains")
DIRECT_FILTER_FIELDS = ("category", "event_type", "owner_type", "amount")
TIME_WINDOW_KINDS = ("current_period", "trailing", "same_period_last_year", "fixed_range", "all_time")
GOAL_QUERY_PERIODS = ("daily", "weekly", "monthly")

_METADATA_FIELD_RE = re.compile(r"^metadata\.[A-Za-z0-9_]+$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def is_valid_aggregation(value) -> bool:
    return isinstance(value, str) and value in AGGREGATIONS


def is_valid_filter_operator(value) -> bool:
    return isinstance(value, str) and value in FILTER_OPERATORS


def is_valid_filter_field(value) -> bool:
    if not isinstance(value, str):
        return False
    return value in DIRECT_FILTER_FIELDS or bool(_METADATA_FIELD_RE.match(value))


def is_valid_time_window_kind(value) -> bool:
    return isinstance(value, str) and value in TIME_WINDOW_KINDS


def is_valid_period(value) -> bool:
    return isinstance(value, str) and value in GOAL_QUERY_PERIODS


def _is_date_string(value) -> bool:
    return isinstance(value, str) and bool(_DATE_RE.match(value))


_MEASURE_FIELD_RE = re.compile(r"^nutrient:.+$")


def is_valid_measure_field(value) -> bool:
    return value is None or (isinstance(value, str) and bool(_MEASURE_FIELD_RE.match(value)))


@dataclass
class FilterCondition:
    field: str
    operator: str
    value: Union[str, float, list]


@dataclass
class TimeWindow:
    kind: str
    period: Optional[str] = None
    count: Optional[int] = None
    start: Optional[str] = None
    end: Optional[str] = None


@dataclass
class GoalQuery:
    aggregation: str
    filters: list[FilterCondition] = dc_field(default_factory=list)
    time_window: Optional[TimeWindow] = None
    percentile: Optional[float] = None
    # None (the default) aggregates over domain_events.amount, same as
    # every finance-tracker goal and this tracker's own calorie-based
    # goals. "nutrient:<Name>" instead aggregates over nutrient_facts.value
    # for that nutrient name, joined to domain_events by (owner_type,
    # owner_id) -- the extension that makes macro/micronutrient targets
    # (protein grams, sodium mg, etc.) expressible, since those live in
    # nutrient_facts, not on the domain_events row itself (see
    # RECURRING_AND_GOALS_SPEC.md's 2026-09-23 "known limitation" note,
    # closed 2026-09-24 by this field). Additive and backward-compatible:
    # every existing goal (finance's, and this tracker's own pre-existing
    # calorie goals) simply never sets it and behaves exactly as before.
    measure_field: Optional[str] = None


def _validate_filter(f: dict, index: int) -> Optional[str]:
    if not isinstance(f, dict):
        return f"filters[{index}] must be an object"
    if not is_valid_filter_field(f.get("field")):
        return f"filters[{index}].field must be one of: {', '.join(DIRECT_FILTER_FIELDS)}, or metadata.<key>"
    if not is_valid_filter_operator(f.get("operator")):
        return f"filters[{index}].operator must be one of: {', '.join(FILTER_OPERATORS)}"
    if f.get("operator") == "in":
        if not isinstance(f.get("value"), list) or len(f["value"]) == 0:
            return f"filters[{index}].value must be a non-empty array when operator is \"in\""
    elif not isinstance(f.get("value"), (str, int, float)):
        return f"filters[{index}].value must be a string or number"
    return None


def _validate_time_window(tw: dict) -> Optional[str]:
    if not isinstance(tw, dict):
        return "timeWindow must be an object"
    kind = tw.get("kind")
    if not is_valid_time_window_kind(kind):
        return f"timeWindow.kind must be one of: {', '.join(TIME_WINDOW_KINDS)}"
    if kind in ("current_period", "trailing", "same_period_last_year"):
        if not is_valid_period(tw.get("period")):
            return f"timeWindow.period must be one of: {', '.join(GOAL_QUERY_PERIODS)} when kind is \"{kind}\""
    if kind in ("trailing", "same_period_last_year"):
        count = tw.get("count")
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            return f"timeWindow.count must be a positive integer when kind is \"{kind}\""
    if kind == "fixed_range":
        if not _is_date_string(tw.get("start")) or not _is_date_string(tw.get("end")):
            return "timeWindow.start and timeWindow.end (YYYY-MM-DD) are required when kind is \"fixed_range\""
    return None


def parse_goal_query(value) -> Union[GoalQuery, str]:
    """Full validation of a GoalQuery from untrusted JSON (a request body,
    or a jsonb column read back). Returns the parsed query or an error
    string naming the first problem -- never raises, matching this
    project's request-validation convention elsewhere. Accepts either
    `timeWindow` (camelCase, the real wire format every tracker's own
    goals already use -- see finance-tracker's RECURRING_AND_GOALS_SPEC.md
    correction, 2026-09-20) or `time_window` (snake_case) on input, same
    lenient-input/canonical-output shape goal-query.ts's own
    parseGoalQuery already established."""
    if not isinstance(value, dict):
        return "must be an object"

    aggregation = value.get("aggregation")
    if not is_valid_aggregation(aggregation):
        return f"aggregation must be one of: {', '.join(AGGREGATIONS)}"
    percentile = value.get("percentile")
    if aggregation == "percentile":
        if not isinstance(percentile, (int, float)) or isinstance(percentile, bool) or not (0 <= percentile <= 100):
            return "percentile is required (0-100) when aggregation is \"percentile\""

    filters = value.get("filters")
    if not isinstance(filters, list):
        return "filters must be an array"
    for i, f in enumerate(filters):
        err = _validate_filter(f, i)
        if err:
            return err

    raw_tw = value.get("timeWindow", value.get("time_window"))
    tw_err = _validate_time_window(raw_tw)
    if tw_err:
        return tw_err

    measure_field = value.get("measureField")
    if not is_valid_measure_field(measure_field):
        return "measureField must be \"nutrient:<Name>\" or omitted"

    return GoalQuery(
        aggregation=aggregation,
        percentile=percentile if aggregation == "percentile" else None,
        filters=[FilterCondition(field=f["field"], operator=f["operator"], value=f["value"]) for f in filters],
        time_window=TimeWindow(**{k: v for k, v in raw_tw.items() if k in ("kind", "period", "count", "start", "end")}),
        measure_field=measure_field,
    )


def goal_query_to_json(q: GoalQuery) -> dict:
    """The canonical, stored/returned JSON shape -- always camelCase
    timeWindow, matching the real cross-tracker wire format."""
    out = {
        "aggregation": q.aggregation,
        "filters": [{"field": f.field, "operator": f.operator, "value": f.value} for f in q.filters],
        "timeWindow": {k: v for k, v in vars(q.time_window).items() if v is not None},
    }
    if q.percentile is not None:
        out["percentile"] = q.percentile
    if q.measure_field is not None:
        out["measureField"] = q.measure_field
    return out


# ── Filter -> SQL condition translation ─────────────────────────────────

_DIRECT_COLUMNS = {
    "category": "de.category",
    "event_type": "de.event_type",
    "owner_type": "de.owner_type",
    "amount": "de.amount",
}

_SQL_OPERATORS = {"eq": "=", "ne": "!=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}


def _build_filter_sql(f: FilterCondition, params: list) -> str:
    """Appends f's value(s) to `params` and returns the SQL boolean
    fragment referencing them positionally ($N) -- `field`/`operator`
    themselves are never interpolated as raw text, only used to select
    which hardcoded column/operator string to emit, matching
    sql_builder.py's identifier-vs-value trust boundary."""
    is_metadata = f.field.startswith("metadata.")
    key = f.field[len("metadata."):] if is_metadata else None
    is_numeric_op = f.operator in ("gt", "gte", "lt", "lte")

    # Built inline per-branch below rather than a shared `column_sql` var,
    # since the metadata extraction needs its OWN placeholder for `key`
    # (a bound parameter, even though _METADATA_FIELD_RE already
    # constrains it) interleaved with the value's placeholder.

    if f.operator == "in":
        if is_metadata:
            params.append(key)
            key_idx = len(params)
            placeholders = []
            for v in f.value:
                params.append(v)
                placeholders.append(f"${len(params)}")
            cast = "::numeric" if is_numeric_op else ""
            expr = f"(de.metadata_json::jsonb ->> ${key_idx}){cast}"
            return f"{expr} IN ({', '.join(placeholders)})"
        else:
            column = _DIRECT_COLUMNS[f.field]
            placeholders = []
            for v in f.value:
                params.append(v)
                placeholders.append(f"${len(params)}")
            return f"{column} IN ({', '.join(placeholders)})"

    if f.operator == "contains":
        if is_metadata:
            params.append(key)
            key_idx = len(params)
            params.append(f"%{f.value}%")
            return f"(de.metadata_json::jsonb ->> ${key_idx}) ILIKE ${len(params)}"
        column = _DIRECT_COLUMNS[f.field]
        params.append(f"%{f.value}%")
        return f"{column} ILIKE ${len(params)}"

    sql_op = _SQL_OPERATORS[f.operator]
    if is_metadata:
        params.append(key)
        key_idx = len(params)
        cast = "::numeric" if is_numeric_op else ""
        params.append(f.value)
        return f"(de.metadata_json::jsonb ->> ${key_idx}){cast} {sql_op} ${len(params)}"
    column = _DIRECT_COLUMNS[f.field]
    params.append(f.value)
    return f"{column} {sql_op} ${len(params)}"


# ── Time window -> date ranges ────────────────────────────────────────────

@dataclass
class DateRange:
    from_: datetime
    to: datetime  # exclusive upper bound
    month_for_cpi: Optional[str] = None  # unused by nutrition (no inflation adjustment) but kept for shape parity


def _period_range(period: str, periods_back: int, now: datetime) -> DateRange:
    if period == "monthly":
        year = now.year
        month0 = now.month - 1 - periods_back  # 0-indexed month, may go negative/large
        year += month0 // 12
        month0 = month0 % 12
        start = datetime(year, month0 + 1, 1, tzinfo=timezone.utc)
        end_month0 = month0 + 1
        end_year = year + (end_month0 // 12)
        end_month0 = end_month0 % 12
        end = datetime(end_year, end_month0 + 1, 1, tzinfo=timezone.utc)
        return DateRange(from_=start, to=end, month_for_cpi=start.strftime("%Y-%m-%d"))

    if period == "daily":
        today = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        to = today - timedelta(days=periods_back) + timedelta(days=1)
        from_ = to - timedelta(days=1)
        return DateRange(from_=from_, to=to)

    # weekly -- week starts Sunday, matching finance's own convention
    weekday = (now.weekday() + 1) % 7  # Python Monday=0 -> Sunday=0
    week_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc) - timedelta(days=weekday)
    to = week_start - timedelta(weeks=periods_back) + timedelta(weeks=1)
    from_ = to - timedelta(weeks=1)
    return DateRange(from_=from_, to=to)


def _same_period_years_ago(period: str, years_back: int, now: datetime) -> DateRange:
    try:
        shifted = now.replace(year=now.year - years_back)
    except ValueError:
        # Feb 29 with no matching date `years_back` years earlier.
        shifted = now.replace(year=now.year - years_back, day=28)
    return _period_range(period, 0, shifted)


def resolve_time_window(tw: TimeWindow, now: Optional[datetime] = None) -> list[DateRange]:
    now = now or datetime.now(timezone.utc)
    if tw.kind == "current_period":
        return [_period_range(tw.period, 0, now)]
    if tw.kind == "trailing":
        return [_period_range(tw.period, i, now) for i in range(1, tw.count + 1)]
    if tw.kind == "same_period_last_year":
        return [_same_period_years_ago(tw.period, i, now) for i in range(1, tw.count + 1)]
    if tw.kind == "fixed_range":
        start = datetime.fromisoformat(f"{tw.start}T00:00:00+00:00")
        end = datetime.fromisoformat(f"{tw.end}T00:00:00+00:00") + timedelta(days=1)  # end is inclusive in the request
        return [DateRange(from_=start, to=end)]
    # all_time
    return [DateRange(from_=datetime(1970, 1, 1, tzinfo=timezone.utc), to=datetime(9999, 1, 1, tzinfo=timezone.utc))]


# ── Current-state deduplication ───────────────────────────────────────────

def _current_state_cte(user_id_param: int, exclude_event_id_param: Optional[int]) -> str:
    """domain_events is an append-only CRUD log -- a row per create/update/
    delete, not one row per owner. Summing `amount` directly over it
    double-counts every owner that's ever been edited more than once (the
    exact bug finance-tracker found live, 2026-09-19, in its own copy of
    this same design). The fix, ported verbatim: a CTE that collapses
    each CRUD-tracked owner (event_type ending in _created/_updated/
    _deleted) down to its single latest row, dropping owners whose latest
    row is a deletion. "Occurrence" events (event_type not matching that
    suffix, e.g. goal_met/goal_exceeded) are independent, repeatable
    facts kept in full, one row each.

    The CTE is named `domain_events`, shadowing the real table for the
    rest of the query (standard Postgres CTE scoping) -- every column
    reference below resolves against this CTE unchanged.

    `$1`/`$2` refer to positional params the caller has ALREADY placed at
    those exact indices (user_id, then optionally excludeEventId) -- this
    function doesn't append to the params list itself, since the same
    user_id/exclude_event_id values are reused both here and in the outer
    WHERE clause, and asyncpg lets the same $N be referenced any number
    of times in one query."""
    exclude_sql = f"AND id != ${exclude_event_id_param}" if exclude_event_id_param else ""
    return f"""
    latest_state AS (
      SELECT DISTINCT ON (owner_type, owner_id) *
      FROM domain_events
      WHERE user_id = ${user_id_param}
        AND event_type ~ '_(created|updated|deleted)$'
        {exclude_sql}
      ORDER BY owner_type, owner_id, logged_at DESC, id DESC
    ),
    domain_events AS (
      SELECT * FROM latest_state WHERE event_type !~ '_deleted$'
      UNION ALL
      SELECT * FROM domain_events
      WHERE user_id = ${user_id_param}
        AND event_type !~ '_(created|updated|deleted)$'
        {exclude_sql}
    )
    """


async def compute_aggregate_for_range(
    conn, user_id: int, query: GoalQuery, range_: DateRange, exclude_event_id: Optional[int] = None
) -> float:
    """Runs one aggregation over one date range, for one user, with the
    query's filters applied -- the only place that actually issues a SQL
    query in this module. Reads through the deduplicated CTE view, never
    the raw domain_events table directly (see _current_state_cte).
    `exclude_event_id`, when set, excludes that one domain_events row --
    used for the event-triggered "before" evaluation, which must compute
    what the aggregate would have been without the just-inserted event."""
    params: list = [user_id]
    exclude_idx: Optional[int] = None
    if exclude_event_id is not None:
        params.append(exclude_event_id)
        exclude_idx = len(params)

    cte = _current_state_cte(1, exclude_idx)

    params.append(range_.from_)
    from_idx = len(params)
    params.append(range_.to)
    to_idx = len(params)

    conditions = [f"de.user_id = $1", f"de.occurred_at >= ${from_idx}", f"de.occurred_at < ${to_idx}"]
    for f in query.filters:
        conditions.append(_build_filter_sql(f, params))
    where_sql = " AND ".join(conditions)

    # measure_field selects WHAT gets aggregated: domain_events.amount by
    # default (every finance goal, this tracker's own calorie goals), or
    # a nutrient_facts.value join for "nutrient:<Name>" (macro/micronutrient
    # targets -- see GoalQuery.measure_field's own doc). The CTE-deduped
    # `domain_events` relation is aliased `de` either way so both branches
    # share one FROM clause shape; nutrient_facts is joined on the SAME
    # (owner_type, owner_id) pair the dedup CTE already resolved to each
    # owner's single latest surviving row, so an edited entry's nutrient
    # values can't double-count any more than its calories can.
    from_sql = "FROM domain_events de"
    value_expr = "de.amount"
    if query.measure_field and query.measure_field.startswith("nutrient:"):
        nutrient_name = query.measure_field[len("nutrient:"):]
        params.append(nutrient_name)
        nutrient_idx = len(params)
        from_sql += f" JOIN nutrient_facts nf ON nf.owner_type = de.owner_type AND nf.owner_id = de.owner_id AND nf.nutrient_name = ${nutrient_idx}"
        value_expr = "nf.value"

    if query.aggregation == "count":
        select_expr = "count(*)"
    elif query.aggregation == "sum":
        select_expr = f"coalesce(sum({value_expr}), 0)"
    elif query.aggregation == "mean":
        select_expr = f"coalesce(avg({value_expr}), 0)"
    elif query.aggregation == "min":
        select_expr = f"coalesce(min({value_expr}), 0)"
    elif query.aggregation == "max":
        select_expr = f"coalesce(max({value_expr}), 0)"
    else:
        fraction = 0.5 if query.aggregation == "median" else query.percentile / 100
        params.append(fraction)
        select_expr = f"coalesce(percentile_cont(${len(params)}) within group (order by {value_expr}), 0)"

    sql = f"WITH {cte} SELECT {select_expr} AS v {from_sql} WHERE {where_sql}"
    row = await conn.fetchrow(sql, *params)
    return float(row["v"]) if row and row["v"] is not None else 0.0


async def compute_measure_for_date(conn, user_id: int, query: GoalQuery, date_str: str, exclude_event_id: Optional[int] = None) -> float:
    """Evaluates a GoalQuery's measure over exactly one calendar date,
    ignoring the query's OWN configured time window entirely -- the
    dashboard's per-date progress view (routers/targets.py's GET
    /progress, rewired 2026-09-24 to read from goals) needs "how much of
    X happened on THIS specific date" for a date the user is actively
    navigating to (today, yesterday, any day), which is a different
    question from a goal's own "current period relative to now" framing
    that evaluate_goal_query answers. Reuses compute_aggregate_for_range
    directly with an explicit single-day range instead."""
    start = datetime.fromisoformat(f"{date_str}T00:00:00+00:00")
    range_ = DateRange(from_=start, to=start + timedelta(days=1))
    return await compute_aggregate_for_range(conn, user_id, query, range_, exclude_event_id)


@dataclass
class EvaluatedQuery:
    value: float
    ranges: list[DateRange]


async def evaluate_goal_query(conn, user_id: int, query: GoalQuery, now: Optional[datetime] = None, exclude_event_id: Optional[int] = None) -> EvaluatedQuery:
    """The full evaluation of one GoalQuery: resolves its time window into
    one or more date ranges, computes the aggregation over each range
    separately, and averages the per-range results together."""
    ranges = resolve_time_window(query.time_window, now)
    values = [await compute_aggregate_for_range(conn, user_id, query, r, exclude_event_id) for r in ranges]
    value = sum(values) / len(values)
    return EvaluatedQuery(value=round(value, 2), ranges=ranges)


def could_match_event(query: GoalQuery, category: Optional[str], event_type: str, owner_type: str, action: str) -> bool:
    """A quick, in-process (not DB-indexed) check of whether an event
    could plausibly match a GoalQuery's filters -- ported verbatim from
    goal-query.ts's couldMatchEvent, including its `action == "updated"`
    escape hatch (an update event only carries the entity's NEW state, so
    it can't be cheaply ruled out the way created/deleted can -- see that
    file's own comment for the full rationale)."""
    if action == "updated":
        return True

    field_values = {"category": category, "event_type": event_type, "owner_type": owner_type, "amount": None}
    for f in query.filters:
        if f.operator not in ("eq", "in"):
            continue
        if f.field == "amount" or f.field.startswith("metadata."):
            continue
        actual = field_values.get(f.field)
        if f.operator == "eq" and actual != f.value:
            return False
        if f.operator == "in" and actual not in f.value:
            return False
    return True


async def compute_nutrient_totals_for_date(conn, user_id: int, date_str: str) -> dict[str, float]:
    """Every nutrient's total consumed on one calendar date, in a single
    query -- the dashboard's progress view needs ~24 nutrients per
    request, and running compute_measure_for_date once per nutrient would
    mean ~24 passes over the dedup CTE. Same semantics as a
    `measureField: "nutrient:<Name>"` goal restricted to
    owner_type = 'food_log' (which is what every DRI/user-target goal's
    measure_query filters on -- pantry stock, recipes, and custom foods
    also carry nutrient_facts rows but aren't consumption), just grouped
    by nutrient instead of filtered to one."""
    start = datetime.fromisoformat(f"{date_str}T00:00:00+00:00")
    end = start + timedelta(days=1)
    cte = _current_state_cte(1, None)
    sql = f"""WITH {cte}
        SELECT nf.nutrient_name, coalesce(sum(nf.value), 0) AS total
        FROM domain_events de
        JOIN nutrient_facts nf ON nf.owner_type = de.owner_type AND nf.owner_id = de.owner_id
        WHERE de.user_id = $1 AND de.owner_type = 'food_log' AND de.occurred_at >= $2 AND de.occurred_at < $3
        GROUP BY nf.nutrient_name"""
    rows = await conn.fetch(sql, user_id, start, end)
    return {r["nutrient_name"]: float(r["total"]) for r in rows}

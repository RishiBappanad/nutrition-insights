"""Per-user SQLite database for nutrition and lift data."""
import sqlite3
from pathlib import Path


def get_user_db(user_data_dir: Path) -> sqlite3.Connection:
    db_path = user_data_dir / "data.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS daily_nutrition (
            date TEXT NOT NULL,
            metric TEXT NOT NULL,
            value REAL NOT NULL,
            PRIMARY KEY (date, metric)
        );

        CREATE TABLE IF NOT EXISTS lift_orm (
            date TEXT NOT NULL,
            exercise TEXT NOT NULL,
            orm REAL NOT NULL,
            PRIMARY KEY (date, exercise)
        );

        CREATE INDEX IF NOT EXISTS idx_nutrition_metric ON daily_nutrition(metric, date);
        CREATE INDEX IF NOT EXISTS idx_orm_exercise ON lift_orm(exercise, date);
    """)
    conn.commit()


def upsert_daily_nutrition(conn: sqlite3.Connection, date: str, metrics: dict):
    """Insert/replace nutrition metrics for a date. metrics = {col_name: value}"""
    conn.executemany(
        "INSERT OR REPLACE INTO daily_nutrition (date, metric, value) VALUES (?, ?, ?)",
        [(date, k, v) for k, v in metrics.items() if v is not None]
    )
    conn.commit()


def upsert_lift_orm(conn: sqlite3.Connection, date: str, exercise: str, orm: float):
    """Insert/replace ORM for an exercise on a date (keeps max)."""
    existing = conn.execute(
        "SELECT orm FROM lift_orm WHERE date = ? AND exercise = ?", (date, exercise)
    ).fetchone()
    if existing is None or orm > existing["orm"]:
        conn.execute(
            "INSERT OR REPLACE INTO lift_orm (date, exercise, orm) VALUES (?, ?, ?)",
            (date, exercise, orm)
        )
        conn.commit()


def query_nutrition(conn: sqlite3.Connection, metrics: list, lookback: int = 1) -> dict:
    """Query nutrition metrics with optional rolling average.
    Returns {metric: [{date, value}]}"""
    result = {}
    for metric in metrics:
        if lookback <= 1:
            rows = conn.execute(
                "SELECT date, value FROM daily_nutrition WHERE metric = ? ORDER BY date",
                (metric,)
            ).fetchall()
            result[metric] = [{"date": r["date"], "value": round(r["value"], 1)} for r in rows]
        else:
            rows = conn.execute("""
                SELECT date,
                    AVG(value) OVER (
                        ORDER BY date
                        ROWS BETWEEN ? PRECEDING AND CURRENT ROW
                    ) as avg_value
                FROM daily_nutrition
                WHERE metric = ?
                ORDER BY date
            """, (lookback - 1, metric)).fetchall()
            result[metric] = [{"date": r["date"], "value": round(r["avg_value"], 1)} for r in rows]
    return result


def query_orm(conn: sqlite3.Connection, exercise: str = None) -> dict:
    """Query ORM data. If exercise specified, returns [{date, orm}].
    Otherwise returns {exercise: [{date, orm}]}."""
    if exercise:
        rows = conn.execute(
            "SELECT date, orm FROM lift_orm WHERE exercise = ? ORDER BY date",
            (exercise,)
        ).fetchall()
        return {exercise: [{"date": r["date"], "value": r["orm"]} for r in rows]}
    else:
        rows = conn.execute("SELECT DISTINCT exercise FROM lift_orm ORDER BY exercise").fetchall()
        result = {}
        for r in rows:
            ex = r["exercise"]
            data = conn.execute(
                "SELECT date, orm FROM lift_orm WHERE exercise = ? ORDER BY date", (ex,)
            ).fetchall()
            result[ex] = [{"date": d["date"], "value": d["orm"]} for d in data]
        return result


def get_exercises(conn: sqlite3.Connection) -> list:
    rows = conn.execute("SELECT DISTINCT exercise FROM lift_orm ORDER BY exercise").fetchall()
    return [r["exercise"] for r in rows]


def get_nutrition_metrics(conn: sqlite3.Connection) -> list:
    rows = conn.execute("SELECT DISTINCT metric FROM daily_nutrition ORDER BY metric").fetchall()
    return [r["metric"] for r in rows]

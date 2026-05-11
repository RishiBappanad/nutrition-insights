"""
SQLite Database Schema for Nutrition Insights Analytics Engine

Normalized schema linking:
- Daily Nutrition (Cronometer)
- Cardio Activities (Strava)
- Weightlifting Sessions (Hevy)
"""

import sqlite3
from pathlib import Path
from typing import Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class DatabaseSchema:
    """Manages SQLite database schema initialization and migrations."""

    SCHEMA_VERSION = 1

    SQL_CREATE_TABLES = """
    -- Daily Nutrition Table (Cronometer)
    CREATE TABLE IF NOT EXISTS daily_nutrition (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL UNIQUE,
        calories REAL,
        protein_g REAL,
        carbs_g REAL,
        fat_g REAL,
        fiber_g REAL,
        sugar_g REAL,
        sodium_mg REAL,
        potassium_mg REAL,
        calcium_mg REAL,
        iron_mg REAL,
        vitamin_d_iu REAL,
        magnesium_mg REAL,
        zinc_mg REAL,  -- Add zinc as a new micronutrient
        -- Add more micronutrients as needed
        raw_csv_path TEXT,
        imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- Cardio Activities Table (Strava)
    CREATE TABLE IF NOT EXISTS cardio_activities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        strava_activity_id INTEGER UNIQUE NOT NULL,
        activity_date DATE NOT NULL,
        activity_type TEXT NOT NULL,  -- e.g., "Run", "Ride", "Swim"
        name TEXT,
        distance_m REAL,
        duration_seconds INTEGER,
        avg_speed_ms REAL,
        max_speed_ms REAL,
        elevation_gain_m REAL,
        avg_heartrate INTEGER,
        max_heartrate INTEGER,
        total_elevation_loss_m REAL,
        calories_burned REAL,
        strava_json_data TEXT,  -- Store raw JSON for reference
        imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- Weightlifting Sessions Table (Hevy)
    CREATE TABLE IF NOT EXISTS weightlifting_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        hevy_session_id TEXT UNIQUE NOT NULL,
        session_date DATE NOT NULL,
        start_time TIMESTAMP,
        end_time TIMESTAMP,
        duration_minutes INTEGER,
        notes TEXT,
        hevy_json_data TEXT,  -- Store raw JSON for reference
        imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- Weightlifting Exercises (exercises within a session)
    CREATE TABLE IF NOT EXISTS weightlifting_exercises (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER NOT NULL,
        hevy_exercise_id TEXT,
        exercise_name TEXT NOT NULL,  -- e.g., "Back Squat", "Bench Press"
        muscle_group TEXT,  -- e.g., "Legs", "Chest", "Back"
        is_superset BOOLEAN DEFAULT 0,
        hevy_json_data TEXT,  -- Store raw JSON for reference
        imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (session_id) REFERENCES weightlifting_sessions(id)
    );

    -- Exercise Sets (individual sets within an exercise)
    CREATE TABLE IF NOT EXISTS exercise_sets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        exercise_id INTEGER NOT NULL,
        set_number INTEGER NOT NULL,
        is_warmup BOOLEAN DEFAULT 0,
        reps INTEGER,
        weight_kg REAL,
        rpe REAL,  -- Rate of Perceived Exertion (1-10)
        notes TEXT,
        hevy_json_data TEXT,
        imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (exercise_id) REFERENCES weightlifting_exercises(id)
    );

    -- Correlations/Insights Table (join table for queries)
    CREATE TABLE IF NOT EXISTS daily_stats (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date DATE NOT NULL UNIQUE,
        nutrition_id INTEGER,
        avg_cardio_pace REAL,  -- Cached: average pace of all cardio that day
        total_cardio_calories REAL,
        total_cardio_distance_m REAL,
        cardio_activity_count INTEGER,
        max_cardio_heartrate INTEGER,
        lifting_session_count INTEGER,
        lifting_duration_minutes INTEGER,
        total_sets_completed INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (nutrition_id) REFERENCES daily_nutrition(id)
    );

    -- Indexes for common queries
    CREATE INDEX IF NOT EXISTS idx_daily_nutrition_date ON daily_nutrition(date);
    CREATE INDEX IF NOT EXISTS idx_cardio_activities_date ON cardio_activities(activity_date);
    CREATE INDEX IF NOT EXISTS idx_weightlifting_sessions_date ON weightlifting_sessions(session_date);
    CREATE INDEX IF NOT EXISTS idx_daily_stats_date ON daily_stats(date);
    """

    def __init__(self, db_path: str = "nutrition_insights.db"):
        """Initialize database connection and schema."""
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def init_database(self) -> None:
        """Create database and all tables if they don't exist."""
        try:
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()

            # Execute schema creation
            cursor.executescript(self.SQL_CREATE_TABLES)

            conn.commit()
            logger.info(f"Database initialized at {self.db_path}")
        except sqlite3.Error as e:
            logger.error(f"Database initialization error: {e}")
            raise
        finally:
            if conn:
                conn.close()

    def get_connection(self) -> sqlite3.Connection:
        """Get a database connection with row factory."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row  # Enable column access by name
        return conn

    def upsert_daily_nutrition(
        self,
        conn: sqlite3.Connection,
        date: str,
        nutrition_data: dict,
        csv_path: Optional[str] = None,
    ) -> int:
        """
        Insert or update daily nutrition data.

        Args:
            conn: Database connection
            date: Date in YYYY-MM-DD format
            nutrition_data: Dictionary with nutrition values
            csv_path: Path to source CSV file

        Returns:
            Row ID of inserted/updated record
        """
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT INTO daily_nutrition (
                date, calories, protein_g, carbs_g, fat_g, fiber_g,
                sugar_g, sodium_mg, potassium_mg, calcium_mg, iron_mg,
                vitamin_d_iu, magnesium_mg, raw_csv_path, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(date) DO UPDATE SET
                calories = excluded.calories,
                protein_g = excluded.protein_g,
                carbs_g = excluded.carbs_g,
                fat_g = excluded.fat_g,
                fiber_g = excluded.fiber_g,
                sugar_g = excluded.sugar_g,
                sodium_mg = excluded.sodium_mg,
                potassium_mg = excluded.potassium_mg,
                calcium_mg = excluded.calcium_mg,
                iron_mg = excluded.iron_mg,
                vitamin_d_iu = excluded.vitamin_d_iu,
                magnesium_mg = excluded.magnesium_mg,
                raw_csv_path = excluded.raw_csv_path,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                date,
                nutrition_data.get("calories"),
                nutrition_data.get("protein_g"),
                nutrition_data.get("carbs_g"),
                nutrition_data.get("fat_g"),
                nutrition_data.get("fiber_g"),
                nutrition_data.get("sugar_g"),
                nutrition_data.get("sodium_mg"),
                nutrition_data.get("potassium_mg"),
                nutrition_data.get("calcium_mg"),
                nutrition_data.get("iron_mg"),
                nutrition_data.get("vitamin_d_iu"),
                nutrition_data.get("magnesium_mg"),
                csv_path,
            ),
        )

        return cursor.lastrowid

    def upsert_cardio_activity(
        self,
        conn: sqlite3.Connection,
        strava_id: int,
        activity_data: dict,
    ) -> int:
        """Insert or update cardio activity from Strava."""
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT INTO cardio_activities (
                strava_activity_id, activity_date, activity_type, name,
                distance_m, duration_seconds, avg_speed_ms, max_speed_ms,
                elevation_gain_m, avg_heartrate, max_heartrate,
                total_elevation_loss_m, calories_burned, strava_json_data, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(strava_activity_id) DO UPDATE SET
                distance_m = excluded.distance_m,
                duration_seconds = excluded.duration_seconds,
                avg_speed_ms = excluded.avg_speed_ms,
                max_speed_ms = excluded.max_speed_ms,
                elevation_gain_m = excluded.elevation_gain_m,
                avg_heartrate = excluded.avg_heartrate,
                max_heartrate = excluded.max_heartrate,
                total_elevation_loss_m = excluded.total_elevation_loss_m,
                calories_burned = excluded.calories_burned,
                strava_json_data = excluded.strava_json_data,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                strava_id,
                activity_data.get("activity_date"),
                activity_data.get("activity_type"),
                activity_data.get("name"),
                activity_data.get("distance_m"),
                activity_data.get("duration_seconds"),
                activity_data.get("avg_speed_ms"),
                activity_data.get("max_speed_ms"),
                activity_data.get("elevation_gain_m"),
                activity_data.get("avg_heartrate"),
                activity_data.get("max_heartrate"),
                activity_data.get("total_elevation_loss_m"),
                activity_data.get("calories_burned"),
                activity_data.get("raw_json"),
            ),
        )

        return cursor.lastrowid

    def query_cross_domain_insights(
        self,
        conn: sqlite3.Connection,
        target_date: str,
    ) -> dict:
        """
        Query correlated data across nutrition, cardio, and lifting for a given date.

        Example: "What was my 12-mile run pace on days where my protein intake was >200g?"
        """
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT
                dn.date,
                dn.protein_g,
                dn.calories,
                ca.activity_type,
                ca.activity_date,
                ca.distance_m,
                ca.duration_seconds,
                CASE
                    WHEN ca.duration_seconds > 0 THEN (ca.distance_m / (ca.duration_seconds / 3600.0))
                    ELSE NULL
                END as pace_kmh,
                ca.avg_heartrate,
                ws.session_date,
                we.exercise_name,
                we.muscle_group
            FROM daily_nutrition dn
            LEFT JOIN cardio_activities ca ON DATE(ca.activity_date) = dn.date
            LEFT JOIN weightlifting_sessions ws ON DATE(ws.session_date) = dn.date
            LEFT JOIN weightlifting_exercises we ON ws.id = we.session_id
            WHERE dn.date = ?
            """,
            (target_date,),
        )

        return cursor.fetchall()


def init_database(db_path: str = "nutrition_insights.db") -> DatabaseSchema:
    """Factory function to initialize database."""
    schema = DatabaseSchema(db_path)
    schema.init_database()
    return schema

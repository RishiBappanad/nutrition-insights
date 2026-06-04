"""
Main orchestration script demonstrating the complete pipeline.

This script demonstrates:
1. Database initialization
2. Cronometer CSV extraction via Playwright
3. Strava API authentication and data fetching
4. Data transformation and validation
5. Database upsert operations
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.config import settings
from src.database.schema import DatabaseSchema
from src.data_processing.transform import (
    CronometerTransformer,
    StravaTransformer,
)
from src.integrations.cronometer_rpc import CronometerRPCClient
from src.integrations.strava import StravaClient
from src.logging_config import setup_logging

logger = logging.getLogger(__name__)


def filter_biometrics(csv_path: str) -> None:
    """Remove heart rate rows from biometrics CSV in place."""
    path = Path(csv_path)
    lines = path.read_text().splitlines()
    filtered = [lines[0]] + [l for l in lines[1:] if "Heart Rate" not in l]
    path.write_text("\n".join(filtered) + "\n")
    logger.info(f"Filtered biometrics: {len(lines)} -> {len(filtered)} rows")


def update_tdee_log(cronometer_files: dict) -> None:
    """Update tdee_tracking_log.csv with weights, calories consumed, and active calories burned."""
    import csv
    from collections import defaultdict

    csv_path = "tdee_tracking_log.csv"

    # 1. Parse weight from biometrics (last entry per day)
    weights = {}
    bio_path = cronometer_files.get("biometrics")
    if bio_path:
        with open(bio_path) as f:
            for row in csv.DictReader(f):
                if "Weight" in row["Metric"] and "Apple Health" not in row["Metric"]:
                    weights[row["Day"]] = float(row["Amount"])

    # 2. Parse calories consumed from daily_summary (Total rows only)
    calories_consumed = {}
    summary_path = cronometer_files.get("daily_summary")
    if summary_path:
        with open(summary_path) as f:
            for row in csv.DictReader(f):
                if row["Group"].strip('"') == "Total" and row["Energy (kcal)"]:
                    calories_consumed[row["Date"]] = round(float(row["Energy (kcal)"]), 1)

    # 3. Parse active calories from exercises (sum per day, absolute value)
    active_calories = defaultdict(float)
    exercises_path = cronometer_files.get("exercises")
    if exercises_path:
        with open(exercises_path) as f:
            for row in csv.DictReader(f):
                if row["Calories Burned"]:
                    active_calories[row["Day"]] += abs(float(row["Calories Burned"]))

    # All dates we have data for
    all_dates = set(weights) | set(calories_consumed) | set(active_calories.keys())
    if not all_dates:
        logger.warning("No data to update TDEE log")
        return

    # Load existing CSV if it exists
    existing = {}
    try:
        with open(csv_path) as f:
            for row in csv.DictReader(f):
                existing[row["Date"]] = row
    except FileNotFoundError:
        pass

    # Merge new data
    for date_str in all_dates:
        if date_str not in existing:
            existing[date_str] = {"Date": date_str, "Weight_lbs": "", "Calories_Consumed": "", "Active_Calories_Burned": ""}
        if date_str in weights:
            existing[date_str]["Weight_lbs"] = weights[date_str]
        if date_str in calories_consumed:
            existing[date_str]["Calories_Consumed"] = calories_consumed[date_str]
        if date_str in active_calories:
            existing[date_str]["Active_Calories_Burned"] = round(active_calories[date_str], 1)

    # Write sorted CSV
    rows = sorted(existing.values(), key=lambda r: r["Date"])
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["Date", "Weight_lbs", "Calories_Consumed", "Active_Calories_Burned"])
        writer.writeheader()
        writer.writerows(rows)

    logger.info(f"TDEE log updated: {len(rows)} rows")


def run_cronometer_export(start_date: str, end_date: str) -> Optional[dict[str, str]]:
    """
    Execute Cronometer CSV export via RPC calls.

    Args:
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format

    Returns:
        Dictionary mapping CSV types to file paths
    """
    try:
        logger.info("Starting Cronometer RPC export...")
        
        client = CronometerRPCClient()
        client.login()
        
        results = client.export_all_to_files(start_date, end_date)

        if not any(results.values()):
            logger.error("All Cronometer exports failed")
            return None

        logger.info("Cronometer RPC export completed successfully")
        return results

    except Exception as e:
        logger.error(f"Cronometer RPC export failed: {e}")
        return None


def run_hevy_export(start_date: str, end_date: str) -> Optional[dict[str, str]]:
    """
    Execute Hevy workout data export via Playwright.

    Returns:
        Dictionary with 'workouts' key pointing to CSV path, or None on failure.
    """
    try:
        from src.integrations.hevy_web import export_hevy_data

        logger.info("Starting Hevy workout export...")
        path = export_hevy_data(headless=True)
        if path:
            logger.info("Hevy export completed successfully")
            return {"workouts": path}
        else:
            logger.error("Hevy export failed")
            return None

    except Exception as e:
        logger.error(f"Hevy export failed: {e}")
        return None


async def run_strava_sync(client: StravaClient) -> Optional[list]:
    """
    Fetch and save Strava activities.

    Args:
        client: StravaClient instance

    Returns:
        List of activities or None if failed
    """
    try:
        logger.info("Starting Strava data sync...")
        activities = await client.get_activities(per_page=200)

        if not activities:
            logger.error("No activities retrieved from Strava")
            return None

        logger.info(f"Retrieved {len(activities)} activities from Strava")
        return activities

    except Exception as e:
        logger.error(f"Strava sync failed: {e}")
        return None


def process_cronometer_data(
    csv_path: str | Path,
    db_conn: sqlite3.Connection,
    db_schema: DatabaseSchema,
) -> int:
    """
    Transform and upsert Cronometer data.

    Args:
        csv_path: Path to CSV file
        db_conn: Database connection
        db_schema: DatabaseSchema instance

    Returns:
        Number of records inserted/updated
    """
    try:
        logger.info(f"Processing Cronometer data from {csv_path}")

        transformer = CronometerTransformer()
        records = transformer.transform(csv_path)

        if not records:
            logger.warning("No records transformed from Cronometer CSV")
            return 0

        # Upsert records
        for record in records:
            nutrition_dict = record.model_dump(exclude_none=True)
            db_schema.upsert_daily_nutrition(
                db_conn,
                record.date,
                nutrition_dict,
                csv_path=str(csv_path),
            )

        db_conn.commit()
        logger.info(f"Upserted {len(records)} nutrition records")
        return len(records)

    except Exception as e:
        logger.error(f"Error processing Cronometer data: {e}")
        db_conn.rollback()
        return 0


def process_hevy_data(
    files: dict[str, str],
    db_conn: sqlite3.Connection,
    db_schema: DatabaseSchema,
) -> int:
    """
    Transform and upsert Hevy workout data.

    Args:
        files: Dictionary mapping data types to file paths
        db_conn: Database connection
        db_schema: DatabaseSchema instance

    Returns:
        Number of records inserted/updated
    """
    try:
        import json
        
        total_records = 0
        
        # Process workout summaries
        if 'workouts' in files:
            workouts_file = files['workouts']
            logger.info(f"Processing Hevy workout data from {workouts_file}")
            
            with open(workouts_file, 'r') as f:
                workouts = json.load(f)
                
            for workout in workouts:
                # Create a simple workout record for now
                # TODO: Create proper Hevy workout table in schema
                workout_data = {
                    'id': workout.get('id', ''),
                    'date': workout.get('date', ''),
                    'name': workout.get('name', ''),
                    'duration_seconds': workout.get('duration_seconds', 0),
                    'estimated_volume_kg': workout.get('estimated_volume_kg', 0),
                    'exercise_count': workout.get('exercise_count', 0),
                }
                
                # For now, store as a simple record in daily_nutrition table
                # TODO: Create dedicated workout tables
                db_schema.upsert_daily_nutrition(
                    db_conn,
                    workout_data['date'],
                    {
                        'calories': 0,  # Placeholder
                        'protein_g': 0,
                        'carbs_g': 0,
                        'fat_g': 0,
                    },
                    csv_path=workouts_file,
                )
                
            total_records += len(workouts)
            logger.info(f"Processed {len(workouts)} workout records")
            
        # Process exercise details
        if 'exercises' in files:
            exercises_file = files['exercises']
            logger.info(f"Processing Hevy exercise data from {exercises_file}")
            
            with open(exercises_file, 'r') as f:
                exercises = json.load(f)
                
            # Group exercises by date for summary
            exercise_by_date = {}
            for exercise in exercises:
                date = exercise.get('workout_date', '')
                if date not in exercise_by_date:
                    exercise_by_date[date] = []
                exercise_by_date[date].append(exercise)
                
            total_records += len(exercises)
            logger.info(f"Processed {len(exercises)} exercise records across {len(exercise_by_date)} dates")
            
        db_conn.commit()
        logger.info(f"Upserted {total_records} Hevy records")
        return total_records

    except Exception as e:
        logger.error(f"Error processing Hevy data: {e}")
        db_conn.rollback()
        return 0


def process_strava_data(
    activities: list,
    db_conn: sqlite3.Connection,
    db_schema: DatabaseSchema,
) -> int:
    """
    Transform and upsert Strava data.

    Args:
        activities: List of activity dictionaries from Strava API
        db_conn: Database connection
        db_schema: DatabaseSchema instance

    Returns:
        Number of records inserted/updated
    """
    try:
        logger.info(f"Processing {len(activities)} Strava activities")

        transformer = StravaTransformer()
        record_count = 0

        for activity in activities:
            try:
                strava_id = activity.get("id")
                timestamp = activity.get("start_date_local")

                if not strava_id or not timestamp:
                    continue

                try:
                    dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                    activity_date = dt.strftime("%Y-%m-%d")
                except Exception:
                    continue

                distance_m = activity.get("distance", 0)
                duration_seconds = activity.get("elapsed_time", 0)
                avg_speed_ms = (distance_m / duration_seconds) if duration_seconds > 0 else None

                from src.data_processing.transform import CardioActivity
                record = CardioActivity(
                    strava_activity_id=strava_id,
                    activity_date=activity_date,
                    activity_type=transformer.normalize_activity_type(activity.get("type", "Unknown")),
                    name=activity.get("name"),
                    distance_m=distance_m,
                    duration_seconds=duration_seconds,
                    avg_speed_ms=avg_speed_ms,
                    max_speed_ms=activity.get("max_speed"),
                    elevation_gain_m=activity.get("total_elevation_gain"),
                    avg_heartrate=activity.get("average_heartrate"),
                    max_heartrate=activity.get("max_heartrate"),
                    total_elevation_loss_m=activity.get("total_elevation_loss"),
                    calories_burned=activity.get("calories"),
                    raw_json=json.dumps(activity),
                )

                activity_dict = record.model_dump(exclude_none=True)
                db_schema.upsert_cardio_activity(
                    db_conn,
                    record.strava_activity_id,
                    activity_dict,
                )
                record_count += 1

            except Exception as e:
                logger.warning(f"Error processing activity {activity.get('id')}: {e}")

        db_conn.commit()
        logger.info(f"Upserted {record_count} cardio activities")
        return record_count

    except Exception as e:
        logger.error(f"Error processing Strava data: {e}")
        db_conn.rollback()
        return 0


async def main() -> None:
    """Main orchestration pipeline."""
    # Setup
    setup_logging()
    logger.info("Starting Nutrition Insights Analytics Pipeline")

    # Initialize database
    db_schema = DatabaseSchema(settings.database_path)
    db_schema.init_database()
    logger.info(f"Database initialized at {settings.database_path}")

    db_conn = db_schema.get_connection()

    try:
        # Step 1: Export from Cronometer using RPC (all history)
        from datetime import datetime, timedelta
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = "2026-04-06"  # Account start date
        
        cronometer_files = run_cronometer_export(start_date, end_date)

        if cronometer_files:
            # Filter heart rate noise from biometrics
            if cronometer_files.get("biometrics"):
                filter_biometrics(cronometer_files["biometrics"])

            # Update TDEE tracking log with weights, calories, and active calories
            update_tdee_log(cronometer_files)

            # Recalculate BMR and push back to Cronometer
            from tdee import calculate_bmr
            bmr = calculate_bmr()
            if isinstance(bmr, (int, float)):
                logger.info(f"Calculated BMR: {bmr} kcal")
                try:
                    client = CronometerRPCClient()
                    client.login()
                    client.set_bmr(int(bmr))
                except Exception as e:
                    logger.warning(f"Could not push BMR to Cronometer: {e}")

            if "daily_summary" in cronometer_files:
                process_cronometer_data(
                    cronometer_files["daily_summary"],
                    db_conn,
                    db_schema,
                )

        # Step 2: Export from Hevy using RPC (run in thread to avoid asyncio/Playwright conflict)
        loop = asyncio.get_event_loop()
        hevy_files = await loop.run_in_executor(None, run_hevy_export, start_date, end_date)

        if hevy_files and "error" not in hevy_files:
            process_hevy_data(hevy_files, db_conn, db_schema)

        # Step 3: Sync from Strava
        strava_client = StravaClient()
        activities = await run_strava_sync(strava_client)

        if activities:
            process_strava_data(activities, db_conn, db_schema)

        logger.info("Pipeline completed successfully")

    except Exception as e:
        logger.error(f"Pipeline failed: {e}")

    finally:
        db_conn.close()


if __name__ == "__main__":
    asyncio.run(main())

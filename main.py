"""
Main orchestration script demonstrating the complete pipeline.

This script demonstrates:
1. Database initialization
2. Cronometer CSV extraction via Playwright
3. Strava API authentication and data fetching
4. Data transformation and validation
5. Database upsert operations
"""

import asyncio
import logging
import sqlite3
from pathlib import Path
from typing import Optional

from src.config import settings
from src.database.schema import DatabaseSchema
from src.data_processing.transform import (
    CronometerTransformer,
    StravaTransformer,
)
from src.integrations.cronometer_rpc import CronometerRPCClient
from src.integrations.hevy_web import HevyWebScraper, create_hevy_scraper
from src.integrations.strava import StravaClient
from src.logging_config import setup_logging

logger = logging.getLogger(__name__)


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
    Execute Hevy workout data export via RPC calls.

    Args:
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format

    Returns:
        Dictionary mapping data types to file paths
    """
    try:
        from datetime import datetime
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        
        from src.integrations.hevy_web import HevyWebScraper, create_hevy_scraper
        
        # Use web scraper instead of API client
        scraper = create_hevy_scraper()
        
        # Get credentials from environment
        username = getattr(settings, 'hevy_username', None)
        password = getattr(settings, 'hevy_password', None)
        
        if not username or not password:
            # Try environment variables directly
            import os
            from dotenv import load_dotenv
            load_dotenv()
            username = os.getenv('HEVY_USERNAME')
            password = os.getenv('HEVY_PASSWORD')
        
        if not username or not password:
            logger.error("Hevy credentials not found in environment")
            return None
            
        logger.info("Starting Hevy workout export...")
        
        # Login and export data
        with scraper:
            if scraper.login(username, password):
                results = scraper.export_all_to_files(start_dt, end_dt)
            
            if "error" in results:
                logger.error(f"Hevy export failed: {results['error']}")
                return None
                
            logger.info("Hevy export completed successfully")
            return results
        else:
            logger.error("Hevy login failed")
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
                # Transform individual activity
                records = transformer.transform([activity])  # Type expects list

                for record in records:
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
        # Step 1: Export from Cronometer using RPC
        # Default to last 30 days, but this can be configured
        from datetime import datetime, timedelta
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        
        cronometer_files = run_cronometer_export(start_date, end_date)

        if cronometer_files and "daily_summary" in cronometer_files:
            process_cronometer_data(
                cronometer_files["daily_summary"],
                db_conn,
                db_schema,
            )

        # Step 2: Export from Hevy using RPC
        hevy_files = run_hevy_export(start_date, end_date)
        
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

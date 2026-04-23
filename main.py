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
from src.integrations.cronometer import CronometerAutomation
from src.integrations.strava import StravaClient
from src.logging_config import setup_logging

logger = logging.getLogger(__name__)


async def run_cronometer_export() -> Optional[dict[str, str]]:
    """
    Execute Cronometer CSV export via Playwright automation.

    Returns:
        Dictionary mapping CSV types to file paths
    """
    try:
        async with CronometerAutomation(headless=False) as automation:
            logger.info("Starting Cronometer CSV export...")
            results = await automation.download_all_csvs()

            if "error" in results:
                logger.error(f"Cronometer export failed: {results['error']}")
                return None

            logger.info("Cronometer export completed successfully")
            return results

    except Exception as e:
        logger.error(f"Cronometer export failed: {e}")
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
        # Step 1: Export from Cronometer
        cronometer_files = await run_cronometer_export()

        if cronometer_files and "daily_summary" in cronometer_files:
            process_cronometer_data(
                cronometer_files["daily_summary"],
                db_conn,
                db_schema,
            )

        # Step 2: Sync from Strava
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

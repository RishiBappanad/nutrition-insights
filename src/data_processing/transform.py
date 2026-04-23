"""
Data transformation and cleaning module for nutrition insights.

Handles:
- Cronometer CSV parsing and validation
- Strava JSON parsing and validation
- Data normalization and cleaning
- Preparation for database upsert
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import pandas as pd
from pydantic import BaseModel, Field, validator

logger = logging.getLogger(__name__)


# Pydantic models for data validation
class NutritionData(BaseModel):
    """Validated nutrition data from Cronometer."""

    date: str = Field(..., description="Date in YYYY-MM-DD format")
    calories: Optional[float] = None
    protein_g: Optional[float] = None
    carbs_g: Optional[float] = None
    fat_g: Optional[float] = None
    fiber_g: Optional[float] = None
    sugar_g: Optional[float] = None
    sodium_mg: Optional[float] = None
    potassium_mg: Optional[float] = None
    calcium_mg: Optional[float] = None
    iron_mg: Optional[float] = None
    vitamin_d_iu: Optional[float] = None
    magnesium_mg: Optional[float] = None

    @validator("date")
    def validate_date(cls, v: str) -> str:
        """Ensure date is in YYYY-MM-DD format."""
        try:
            datetime.strptime(v, "%Y-%m-%d")
            return v
        except ValueError:
            raise ValueError(f"Date must be in YYYY-MM-DD format, got {v}")


class CardioActivity(BaseModel):
    """Validated cardio activity from Strava."""

    strava_activity_id: int
    activity_date: str
    activity_type: str
    name: Optional[str] = None
    distance_m: Optional[float] = None
    duration_seconds: Optional[int] = None
    avg_speed_ms: Optional[float] = None
    max_speed_ms: Optional[float] = None
    elevation_gain_m: Optional[float] = None
    avg_heartrate: Optional[int] = None
    max_heartrate: Optional[int] = None
    total_elevation_loss_m: Optional[float] = None
    calories_burned: Optional[float] = None
    raw_json: Optional[str] = None

    @validator("activity_date")
    def validate_activity_date(cls, v: str) -> str:
        """Ensure date is in YYYY-MM-DD format."""
        try:
            datetime.strptime(v, "%Y-%m-%d")
            return v
        except ValueError:
            raise ValueError(f"Date must be in YYYY-MM-DD format, got {v}")


class CronometerTransformer:
    """Transform Cronometer CSV exports into normalized nutrition data."""

    # Mapping of possible column names in Cronometer CSV to standardized names
    COLUMN_MAPPINGS = {
        "Calories": "calories",
        "Protein (g)": "protein_g",
        "Net Carbs (g)": "carbs_g",
        "Total Carbs (g)": "carbs_g",
        "Fat (g)": "fat_g",
        "Fiber (g)": "fiber_g",
        "Sugars (g)": "sugar_g",
        "Sodium (mg)": "sodium_mg",
        "Potassium (mg)": "potassium_mg",
        "Calcium (mg)": "calcium_mg",
        "Iron (mg)": "iron_mg",
        "Vitamin D (IU)": "vitamin_d_iu",
        "Magnesium (mg)": "magnesium_mg",
    }

    def __init__(self):
        """Initialize transformer."""
        self.last_error: Optional[str] = None

    def read_csv(self, filepath: str | Path) -> Optional[pd.DataFrame]:
        """
        Read Cronometer CSV file.

        Args:
            filepath: Path to CSV file

        Returns:
            DataFrame or None if read fails
        """
        try:
            df = pd.read_csv(filepath)
            logger.info(f"Successfully read CSV: {filepath}")
            return df
        except FileNotFoundError:
            self.last_error = f"File not found: {filepath}"
            logger.error(self.last_error)
            return None
        except pd.errors.ParserError as e:
            self.last_error = f"CSV parsing error: {e}"
            logger.error(self.last_error)
            return None
        except Exception as e:
            self.last_error = f"Unexpected error reading CSV: {e}"
            logger.error(self.last_error)
            return None

    def normalize_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Normalize column names from Cronometer's format.

        Args:
            df: Raw DataFrame with Cronometer columns

        Returns:
            DataFrame with normalized column names
        """
        try:
            # Strip whitespace from column names
            df.columns = df.columns.str.strip()

            # Map column names
            rename_dict = {
                col: mapped for col, mapped in self.COLUMN_MAPPINGS.items() if col in df.columns
            }

            df = df.rename(columns=rename_dict)
            logger.info(f"Normalized {len(rename_dict)} columns")
            return df

        except Exception as e:
            logger.error(f"Error normalizing columns: {e}")
            return df

    def clean_numeric_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Clean numeric columns by removing non-numeric values.

        Args:
            df: DataFrame with potentially dirty numeric columns

        Returns:
            Cleaned DataFrame
        """
        numeric_cols = [
            "calories", "protein_g", "carbs_g", "fat_g", "fiber_g",
            "sugar_g", "sodium_mg", "potassium_mg", "calcium_mg",
            "iron_mg", "vitamin_d_iu", "magnesium_mg",
        ]

        for col in numeric_cols:
            if col in df.columns:
                try:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                except Exception as e:
                    logger.warning(f"Error cleaning column {col}: {e}")

        return df

    def transform(
        self,
        filepath: str | Path,
        date_column: str = "Date",
    ) -> list[NutritionData]:
        """
        Transform Cronometer CSV to validated nutrition data.

        Args:
            filepath: Path to CSV file
            date_column: Name of date column in CSV

        Returns:
            List of NutritionData objects
        """
        try:
            # Read CSV
            df = self.read_csv(filepath)
            if df is None:
                return []

            # Normalize columns
            df = self.normalize_columns(df)

            # Clean numeric columns
            df = self.clean_numeric_columns(df)

            # Parse date if present
            if date_column in df.columns:
                try:
                    df[date_column] = pd.to_datetime(df[date_column])
                    df["date"] = df[date_column].dt.strftime("%Y-%m-%d")
                except Exception as e:
                    logger.warning(f"Error parsing date column: {e}")
                    return []
            else:
                logger.error(f"Date column '{date_column}' not found in CSV")
                return []

            # Convert rows to NutritionData objects
            nutrition_records = []
            for idx, row in df.iterrows():
                try:
                    record = NutritionData(
                        date=row["date"],
                        calories=row.get("calories"),
                        protein_g=row.get("protein_g"),
                        carbs_g=row.get("carbs_g"),
                        fat_g=row.get("fat_g"),
                        fiber_g=row.get("fiber_g"),
                        sugar_g=row.get("sugar_g"),
                        sodium_mg=row.get("sodium_mg"),
                        potassium_mg=row.get("potassium_mg"),
                        calcium_mg=row.get("calcium_mg"),
                        iron_mg=row.get("iron_mg"),
                        vitamin_d_iu=row.get("vitamin_d_iu"),
                        magnesium_mg=row.get("magnesium_mg"),
                    )
                    nutrition_records.append(record)
                except Exception as e:
                    logger.warning(f"Skipping row {idx}: {e}")

            logger.info(f"Transformed {len(nutrition_records)} nutrition records")
            return nutrition_records

        except Exception as e:
            logger.error(f"Transformation error: {e}")
            return []


class StravaTransformer:
    """Transform Strava API JSON data into normalized activity records."""

    ACTIVITY_TYPE_MAPPING = {
        "Run": "Run",
        "Ride": "Ride",
        "Swim": "Swim",
        "Walk": "Walk",
        "Hike": "Hike",
        "EBikeRide": "E-Bike Ride",
    }

    def __init__(self):
        """Initialize transformer."""
        self.last_error: Optional[str] = None

    def read_json(self, filepath: str | Path) -> Optional[list[dict[str, Any]]]:
        """
        Read Strava JSON file.

        Args:
            filepath: Path to JSON file (should be array of activities)

        Returns:
            List of activity dicts or None if read fails
        """
        try:
            with open(filepath, "r") as f:
                data = json.load(f)

            if not isinstance(data, list):
                self.last_error = "JSON must contain an array of activities"
                logger.error(self.last_error)
                return None

            logger.info(f"Successfully read JSON: {filepath}")
            return data

        except FileNotFoundError:
            self.last_error = f"File not found: {filepath}"
            logger.error(self.last_error)
            return None
        except json.JSONDecodeError as e:
            self.last_error = f"JSON parsing error: {e}"
            logger.error(self.last_error)
            return None
        except Exception as e:
            self.last_error = f"Unexpected error reading JSON: {e}"
            logger.error(self.last_error)
            return None

    def normalize_activity_type(self, strava_type: str) -> str:
        """
        Normalize Strava activity type.

        Args:
            strava_type: Raw activity type from Strava API

        Returns:
            Normalized activity type
        """
        return self.ACTIVITY_TYPE_MAPPING.get(strava_type, strava_type)

    def extract_timestamp(self, epoch_seconds: int) -> str:
        """
        Convert epoch timestamp to ISO date string.

        Args:
            epoch_seconds: Unix timestamp

        Returns:
            Date string in YYYY-MM-DD format
        """
        try:
            dt = datetime.fromtimestamp(epoch_seconds)
            return dt.strftime("%Y-%m-%d")
        except Exception as e:
            logger.warning(f"Error parsing timestamp {epoch_seconds}: {e}")
            return ""

    def transform(self, filepath: str | Path) -> list[CardioActivity]:
        """
        Transform Strava JSON to validated activity records.

        Args:
            filepath: Path to JSON file

        Returns:
            List of CardioActivity objects
        """
        try:
            # Read JSON
            activities = self.read_json(filepath)
            if activities is None:
                return []

            cardio_records = []

            for activity in activities:
                try:
                    # Extract required fields
                    strava_id = activity.get("id")
                    timestamp = activity.get("start_date_local")

                    if not strava_id or not timestamp:
                        logger.warning(f"Skipping activity: missing id or timestamp")
                        continue

                    # Parse timestamp
                    try:
                        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                        activity_date = dt.strftime("%Y-%m-%d")
                    except Exception as e:
                        logger.warning(f"Error parsing timestamp: {e}")
                        continue

                    # Extract distance (Strava provides in meters)
                    distance_m = activity.get("distance", 0)

                    # Extract duration (Strava provides in seconds)
                    duration_seconds = activity.get("elapsed_time", 0)

                    # Calculate average speed (m/s)
                    avg_speed_ms = None
                    if duration_seconds > 0:
                        avg_speed_ms = distance_m / duration_seconds

                    record = CardioActivity(
                        strava_activity_id=strava_id,
                        activity_date=activity_date,
                        activity_type=self.normalize_activity_type(
                            activity.get("type", "Unknown")
                        ),
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

                    cardio_records.append(record)

                except Exception as e:
                    logger.warning(f"Skipping activity: {e}")

            logger.info(f"Transformed {len(cardio_records)} Strava activities")
            return cardio_records

        except Exception as e:
            logger.error(f"Transformation error: {e}")
            return []


def transform_and_validate_nutrition(
    csv_path: str | Path,
) -> tuple[bool, list[NutritionData]]:
    """
    Convenience function to transform and validate Cronometer data.

    Args:
        csv_path: Path to Cronometer CSV

    Returns:
        Tuple of (success: bool, records: list[NutritionData])
    """
    transformer = CronometerTransformer()
    records = transformer.transform(csv_path)
    success = len(records) > 0
    return success, records


def transform_and_validate_strava(
    json_path: str | Path,
) -> tuple[bool, list[CardioActivity]]:
    """
    Convenience function to transform and validate Strava data.

    Args:
        json_path: Path to Strava JSON export

    Returns:
        Tuple of (success: bool, records: list[CardioActivity])
    """
    transformer = StravaTransformer()
    records = transformer.transform(json_path)
    success = len(records) > 0
    return success, records

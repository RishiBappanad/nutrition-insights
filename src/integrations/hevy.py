"""
Hevy API integration module.

Handles API requests to Hevy for weightlifting data.
"""

import logging
from typing import Optional

import requests

from src.config import settings

logger = logging.getLogger(__name__)


class HevyClient:
    """Client for Hevy Weightlifting API."""

    API_BASE = "https://api.hevy.com/v1"

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize Hevy client.

        Args:
            api_key: Hevy API key
        """
        self.api_key = api_key or settings.hevy_api_key
        self.session = requests.Session()
        self.session.headers.update({"api-key": self.api_key})

        if not self.api_key:
            logger.warning("Hevy API key not configured")

    def get_workouts(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Optional[list[dict]]:
        """
        Get workouts within date range.

        Args:
            start_date: Start date (ISO format)
            end_date: End date (ISO format)

        Returns:
            List of workouts or None if error
        """
        if not self.api_key:
            logger.error("Hevy API key not configured")
            return None

        try:
            params = {}
            if start_date:
                params["start_date"] = start_date
            if end_date:
                params["end_date"] = end_date

            response = self.session.get(
                f"{self.API_BASE}/workouts",
                params=params,
            )

            response.raise_for_status()
            logger.info(f"Retrieved {len(response.json())} workouts from Hevy")
            return response.json()

        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching workouts: {e}")
            return None

    def get_workout_details(self, workout_id: str) -> Optional[dict]:
        """
        Get detailed information about a specific workout.

        Args:
            workout_id: Hevy workout ID

        Returns:
            Workout details or None if error
        """
        if not self.api_key:
            logger.error("Hevy API key not configured")
            return None

        try:
            response = self.session.get(f"{self.API_BASE}/workouts/{workout_id}")
            response.raise_for_status()
            logger.info(f"Retrieved workout details for {workout_id}")
            return response.json()

        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching workout details: {e}")
            return None

"""
Strava API integration module.

Handles OAuth2 authentication and API requests to Strava.
"""

import logging
from typing import Optional

import requests

from src.config import settings

logger = logging.getLogger(__name__)


class StravaClient:
    """Client for Strava REST API."""

    API_BASE = "https://www.strava.com/api/v3"
    OAUTH_TOKEN_URL = "https://www.strava.com/oauth/token"

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        refresh_token: Optional[str] = None,
    ):
        """
        Initialize Strava client.

        Args:
            client_id: Strava OAuth client ID
            client_secret: Strava OAuth client secret
            refresh_token: OAuth refresh token
        """
        self.client_id = client_id or settings.strava_client_id
        self.client_secret = client_secret or settings.strava_client_secret
        self.refresh_token = refresh_token or settings.strava_refresh_token
        self.access_token: Optional[str] = None
        self.session = requests.Session()

    async def refresh_access_token(self) -> bool:
        """
        Refresh OAuth access token using refresh token.

        Returns:
            True if successful
        """
        if not self.refresh_token:
            logger.error("Refresh token not configured")
            return False

        try:
            response = requests.post(
                self.OAUTH_TOKEN_URL,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "grant_type": "refresh_token",
                    "refresh_token": self.refresh_token,
                },
            )

            response.raise_for_status()
            data = response.json()
            self.access_token = data["access_token"]
            logger.info("Successfully refreshed Strava access token")
            return True

        except requests.exceptions.RequestException as e:
            logger.error(f"Error refreshing access token: {e}")
            return False

    async def get_activities(
        self,
        before: Optional[int] = None,
        after: Optional[int] = None,
        per_page: int = 200,
    ) -> Optional[list[dict]]:
        """
        Get athlete activities.

        Args:
            before: Start date (unix epoch)
            after: End date (unix epoch)
            per_page: Pagination size

        Returns:
            List of activities or None if error
        """
        if not self.access_token:
            if not await self.refresh_access_token():
                return None

        try:
            headers = {"Authorization": f"Bearer {self.access_token}"}
            params = {"per_page": per_page}

            if before:
                params["before"] = before
            if after:
                params["after"] = after

            response = requests.get(
                f"{self.API_BASE}/athlete/activities",
                headers=headers,
                params=params,
            )

            response.raise_for_status()
            logger.info(f"Retrieved {len(response.json())} activities from Strava")
            return response.json()

        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching activities: {e}")
            return None

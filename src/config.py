"""
Configuration module using Pydantic for environment variable validation.
"""

from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings from environment variables."""

    # Cronometer
    cronometer_email: Optional[str] = None
    cronometer_password: Optional[str] = None

    # Strava
    strava_client_id: Optional[str] = None
    strava_client_secret: Optional[str] = None
    strava_refresh_token: Optional[str] = None

    # Hevy
    hevy_api_key: Optional[str] = None

    # Database
    database_path: str = "nutrition_insights.db"

    # Logging
    log_level: str = "INFO"

    # Data paths
    raw_data_dir: str = "raw_data"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


settings = Settings()

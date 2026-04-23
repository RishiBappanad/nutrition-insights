"""
Unit tests for data transformation module.
"""

import json
from pathlib import Path

import pytest

from src.data_processing.transform import (
    CronometerTransformer,
    StravaTransformer,
    NutritionData,
)


class TestNutritionData:
    """Test NutritionData validation."""

    def test_valid_nutrition_data(self):
        """Test valid nutrition data creation."""
        data = NutritionData(
            date="2024-01-15",
            calories=2000.0,
            protein_g=150.0,
            carbs_g=250.0,
        )
        assert data.date == "2024-01-15"
        assert data.calories == 2000.0

    def test_invalid_date_format(self):
        """Test date validation rejects bad format."""
        with pytest.raises(ValueError):
            NutritionData(
                date="01-15-2024",  # Wrong format
                calories=2000.0,
            )


class TestCronometerTransformer:
    """Test Cronometer data transformation."""

    def test_column_normalization(self):
        """Test column name normalization."""
        transformer = CronometerTransformer()
        # Test that known columns are mapped correctly
        assert transformer.COLUMN_MAPPINGS["Protein (g)"] == "protein_g"
        assert transformer.COLUMN_MAPPINGS["Calories"] == "calories"

    def test_missing_file(self):
        """Test handling of missing CSV file."""
        transformer = CronometerTransformer()
        result = transformer.read_csv("nonexistent.csv")
        assert result is None
        assert "File not found" in transformer.last_error


class TestStravaTransformer:
    """Test Strava data transformation."""

    def test_activity_type_normalization(self):
        """Test activity type mapping."""
        transformer = StravaTransformer()
        assert transformer.normalize_activity_type("Run") == "Run"
        assert transformer.normalize_activity_type("EBikeRide") == "E-Bike Ride"

    def test_missing_json_file(self):
        """Test handling of missing JSON file."""
        transformer = StravaTransformer()
        result = transformer.read_json("nonexistent.json")
        assert result is None
        assert "File not found" in transformer.last_error

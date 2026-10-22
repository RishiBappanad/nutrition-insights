"""Unit tests for integrations/barcode_lookup.py's lookup_barcode().

Response shapes below are trimmed copies of Open Food Facts' real v2
product API response shape (a top-level `status` + `product` object with
per-100g nutriment keys), not synthetic placeholder data."""
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from integrations.barcode_lookup import lookup_barcode  # noqa: E402


class _FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

    def json(self):
        return self._json_data


def test_found_product_is_normalized(monkeypatch):
    monkeypatch.setattr(
        "integrations.barcode_lookup.requests.get",
        lambda url, timeout: _FakeResponse({
            "status": 1,
            "product": {
                "product_name": "Chobani Greek Yogurt Plain",
                "brands": "Chobani",
                "serving_quantity": 150,
                "serving_quantity_unit": "g",
                "nutriments": {
                    "energy-kcal_100g": 59,
                    "proteins_100g": 10.0,
                    "carbohydrates_100g": 3.6,
                    "fat_100g": 0.4,
                    "sodium_100g": 0.036,  # grams per 100g -> 36mg
                },
            },
        }),
    )
    result = lookup_barcode("00818290010256")
    assert result["source"] == "OpenFoodFacts"
    assert result["name"] == "Chobani Greek Yogurt Plain"
    assert result["brand"] == "Chobani"
    assert result["nutrients"]["Energy"] == {"value": 59, "unit": "KCAL"}
    assert result["nutrients"]["Protein"] == {"value": 10.0, "unit": "G"}
    assert result["nutrients"]["Sodium, Na"] == {"value": 36.0, "unit": "MG"}


def test_not_found_returns_none(monkeypatch):
    monkeypatch.setattr(
        "integrations.barcode_lookup.requests.get",
        lambda url, timeout: _FakeResponse({"status": 0}),
    )
    assert lookup_barcode("00000000000000") is None


def test_missing_product_name_returns_none(monkeypatch):
    # OFF sometimes has a record with no usable name at all -- treated the
    # same as not found rather than surfacing a nameless result.
    monkeypatch.setattr(
        "integrations.barcode_lookup.requests.get",
        lambda url, timeout: _FakeResponse({"status": 1, "product": {"brands": "SomeBrand"}}),
    )
    assert lookup_barcode("12345678") is None


def test_network_failure_returns_none_not_raises(monkeypatch):
    def raise_connection_error(url, timeout):
        raise ConnectionError("network unreachable")

    monkeypatch.setattr("integrations.barcode_lookup.requests.get", raise_connection_error)
    assert lookup_barcode("12345678") is None


def test_missing_nutrients_are_omitted_not_zero(monkeypatch):
    monkeypatch.setattr(
        "integrations.barcode_lookup.requests.get",
        lambda url, timeout: _FakeResponse({
            "status": 1,
            "product": {"product_name": "Mystery Snack", "nutriments": {"proteins_100g": 5}},
        }),
    )
    result = lookup_barcode("99999999")
    assert result["nutrients"] == {"Protein": {"value": 5, "unit": "G"}}
    assert "Energy" not in result["nutrients"]

"""
Barcode scanner: UPC/EAN -> parsed nutrition facts, mirroring
label_scanner.py's contract -- same "never saves anything" rule (the
caller separately POSTs to /custom-foods to persist), same
suggested_matches reconciliation against existing USDA/CNF entries (see
../food_matching.py). The two scan methods differ only in how they
identify a food (photo OCR vs. barcode lookup), not in what happens after.
"""
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..routers.auth import get_current_user
from ..food_matching import find_existing_food_matches
from integrations.barcode_lookup import lookup_barcode

router = APIRouter()


class BarcodeScanRequest(BaseModel):
    barcode: str


class BarcodeScanResult(BaseModel):
    status: str  # "success" | "not_found"
    food_name: Optional[str] = None
    brand: Optional[str] = None
    reference_grams: Optional[float] = None
    calories: Optional[float] = None
    nutrients: dict = {}
    error: Optional[str] = None
    # Existing USDA/CNF foods this barcode's product plausibly matches
    # (see ../food_matching.py), best match first -- lets the caller offer
    # linking to one of these instead of always creating a new
    # custom_foods entry for a product USDA/CNF already has too.
    suggested_matches: list = []


@router.post("/scan", response_model=BarcodeScanResult)
async def scan_barcode(req: BarcodeScanRequest, user_id: int = Depends(get_current_user)):
    product = lookup_barcode(req.barcode)
    if product is None:
        return BarcodeScanResult(status="not_found", error=f"No product found for barcode {req.barcode}")

    energy = product["nutrients"].get("Energy")
    result = BarcodeScanResult(
        status="success",
        food_name=product["name"],
        brand=product["brand"] or None,
        reference_grams=product["serving_size"],
        calories=energy["value"] if energy else None,
        nutrients=product["nutrients"],
    )
    result.suggested_matches = find_existing_food_matches(result.food_name, result.brand)
    return result

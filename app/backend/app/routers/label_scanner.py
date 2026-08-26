"""
Nutrition label scanner: image -> parsed nutrition facts, for creating a
custom_foods entry. Mirrors finance-tracker's Gemini Vision receipt-OCR
pattern (services/receipt-ocr.ts) — same raw-REST-call-to-Gemini approach
(no SDK dependency), same JSON-only extraction prompt style, same
graceful-degradation contract (no API key or a failed extraction returns
a `manual_required` status, never an error the caller has to guess at),
and critically the same rule: **this endpoint never saves anything**. It
returns parsed data for the user to review/edit, then the caller
separately POSTs to /custom-foods (already built) to actually persist it
— scanning and saving are two explicit steps, matching finance-tracker's
receipt /scan + separate confirm flow, not one auto-committing action.

No Gemini/genai dependency existed anywhere in this backend before this
module — confirmed via search. GEMINI_API_KEY must be set in env to
enable it; without it, every call returns manual_required immediately
rather than failing, same contract finance-tracker already established.
"""
import base64
import json
import os
import re
from typing import Optional

import requests
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from ..routers.auth import get_current_user

router = APIRouter()

GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent"
MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20MB, matches finance-tracker's multer limit
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp"}

EXTRACTION_PROMPT = """Analyze this nutrition facts label image and extract structured data. \
Return ONLY valid JSON with this exact schema, no other text:

{
  "food_name": "string or null (product name if visible, else null)",
  "brand": "string or null",
  "reference_amount": number or null,
  "reference_unit": "string or null (e.g. \\"cup\\", \\"piece\\", \\"g\\")",
  "reference_grams": number or null (the serving size in grams, if shown or derivable),
  "calories": number or null,
  "protein": number or null (grams),
  "carbs": number or null (grams, Total Carbohydrate),
  "fat": number or null (grams, Total Fat),
  "nutrients": {
    "Fiber, total dietary": {"value": number, "unit": "G"},
    "Sodium, Na": {"value": number, "unit": "mg"},
    "Sugars, total": {"value": number, "unit": "g"}
  }
}

Rules:
- Only include nutrients actually visible on the label in the "nutrients" object
- Dietary Fiber goes in "nutrients" as "Fiber, total dietary" — it is NOT one \
of the 4 top-level macro fields (calories/protein/carbs/fat)
- Use standard USDA-style nutrient names where recognizable (e.g. "Sodium, Na", \
"Iron, Fe", "Calcium, Ca", "Potassium, K", "Vitamin C, total ascorbic acid", \
"Vitamin D (D2 + D3)") so this matches the naming convention used elsewhere \
in this app's nutrient tracking — if unsure of the exact convention name, \
use the label's own wording instead of guessing a USDA name incorrectly.
- All values should be plain numbers (no units in the number fields)
- If a field isn't visible or legible, use null — do NOT hallucinate values
- calories/protein/carbs/fat refer to the amount for ONE reference serving \
(reference_amount of reference_unit), not per-container"""


class LabelScanResult(BaseModel):
    status: str  # "success" | "partial" | "manual_required" | "failed"
    confidence: float
    food_name: Optional[str] = None
    brand: Optional[str] = None
    reference_amount: Optional[float] = None
    reference_unit: Optional[str] = None
    reference_grams: Optional[float] = None
    calories: Optional[float] = None
    protein: Optional[float] = None
    carbs: Optional[float] = None
    fat: Optional[float] = None
    # Fiber is not a top-level field — it's returned in nutrients under
    # "Fiber, total dietary" like every other non-macro nutrient.
    nutrients: dict = {}
    error: Optional[str] = None


def _manual_required(error: str) -> LabelScanResult:
    return LabelScanResult(status="manual_required", confidence=0.0, error=error)


def _confidence_score(parsed: dict) -> float:
    """Same style of heuristic confidence scoring as finance-tracker's
    receipt-ocr.ts — presence of key fields adds up to a 0-1 score, not a
    fabricated precise probability."""
    score = 0.0
    if parsed.get("food_name"):
        score += 0.25
    if parsed.get("calories") is not None:
        score += 0.25
    if parsed.get("reference_amount") is not None or parsed.get("reference_grams") is not None:
        score += 0.2
    if parsed.get("nutrients"):
        score += 0.3
    return round(score, 2)


def extract_label_with_gemini(image_bytes: bytes, mime_type: str) -> LabelScanResult:
    """Call Gemini Vision directly via REST (no SDK), mirroring
    receipt-ocr.ts's extractWithGemini exactly: raw fetch/requests call,
    same generationConfig (low temperature for extraction consistency),
    same JSON-extraction-from-markdown-fences handling, same graceful
    failure modes (no key / API error / unparseable response / network
    failure all return a structured result, never raise to the caller)."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return _manual_required("GEMINI_API_KEY not configured")

    try:
        response = requests.post(
            f"{GEMINI_ENDPOINT}?key={api_key}",
            json={
                "contents": [{
                    "parts": [
                        {"text": EXTRACTION_PROMPT},
                        {"inlineData": {"mimeType": mime_type, "data": base64.b64encode(image_bytes).decode()}},
                    ],
                }],
                "generationConfig": {"temperature": 0.1, "maxOutputTokens": 2048},
            },
            timeout=30,
        )
    except requests.exceptions.Timeout:
        return LabelScanResult(status="failed", confidence=0.0, error="Gemini request timed out (30s)")
    except requests.exceptions.RequestException as e:
        return LabelScanResult(status="failed", confidence=0.0, error=f"Gemini unavailable: {e}")

    if not response.ok:
        return LabelScanResult(status="failed", confidence=0.0, error=f"Gemini API error: {response.status_code}")

    try:
        text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, ValueError):
        return LabelScanResult(status="failed", confidence=0.0, error="Unexpected Gemini response shape")

    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return LabelScanResult(status="failed", confidence=0.1, error="Could not parse JSON from Gemini response")

    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return LabelScanResult(status="failed", confidence=0.1, error="Malformed JSON from Gemini response")

    nutrients = {}
    for name, info in (parsed.get("nutrients") or {}).items():
        if isinstance(info, dict) and info.get("value") is not None:
            try:
                nutrients[name] = {"value": float(info["value"]), "unit": info.get("unit", "")}
            except (TypeError, ValueError):
                continue

    # Defensive fallback: the prompt asks Gemini to put fiber inside
    # `nutrients`, but its output isn't schema-enforced — if it still
    # returns a stray top-level "fiber" despite the prompt, fold it in
    # rather than silently dropping it.
    legacy_fiber = parsed.get("fiber")
    if legacy_fiber is not None and "Fiber, total dietary" not in nutrients:
        try:
            nutrients["Fiber, total dietary"] = {"value": float(legacy_fiber), "unit": "G"}
        except (TypeError, ValueError):
            pass

    confidence = _confidence_score(parsed)
    return LabelScanResult(
        status="success" if confidence >= 0.5 else "partial",
        confidence=confidence,
        food_name=parsed.get("food_name"),
        brand=parsed.get("brand"),
        reference_amount=parsed.get("reference_amount"),
        reference_unit=parsed.get("reference_unit"),
        reference_grams=parsed.get("reference_grams"),
        calories=parsed.get("calories"),
        protein=parsed.get("protein"),
        carbs=parsed.get("carbs"),
        fat=parsed.get("fat"),
        nutrients=nutrients,
    )


@router.post("/scan", response_model=LabelScanResult)
async def scan_label(file: UploadFile = File(...), user_id: int = Depends(get_current_user)):
    """
    Upload a nutrition label photo, get back parsed nutrition facts for
    review. Does NOT save anything — the caller reviews/edits the result
    and then POSTs to /custom-foods separately to persist it, exactly
    like finance-tracker's receipt /scan followed by a separate confirm
    step. This endpoint is deliberately not the one that writes to the
    database, so a bad scan never silently creates bad data.
    """
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported content type: {file.content_type}")

    contents = await file.read()
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail=f"File too large (max {MAX_UPLOAD_BYTES // (1024*1024)}MB)")
    if len(contents) == 0:
        raise HTTPException(status_code=400, detail="Empty file")

    return extract_label_with_gemini(contents, file.content_type)

"""
Matches a scanned/extracted food name (from label_scanner.py today; any
future source describing a food by name/brand could reuse this) against
existing USDA/CNF entries, so confirming a scan links to a food this app
already knows about instead of always creating a new, disconnected
custom_foods row for a product that's already properly indexed.

Token-set overlap (Jaccard similarity on lowercased word sets), not
sequence similarity (e.g. difflib.SequenceMatcher) -- USDA's naming
convention reverses word order relative to how a label/brand name
naturally reads (e.g. "Yogurt, Greek, plain, CHOBANI" vs. "Chobani Greek
Yogurt Plain"), which tanks a sequence-similarity score for a genuinely
correct match. Token overlap is order-insensitive by construction, tested
against real USDA search results before writing this.
"""
import re
from typing import Dict, List, Optional

from integrations.food_search import search_foods

# Below this, candidates are too weak to call them a match at all --
# nothing scored this low in testing was ever the right food.
MIN_MATCH_SCORE = 0.3


def _tokenize(s: str) -> set:
    return set(re.findall(r"[a-z0-9]+", s.lower()))


def _token_overlap_score(scanned: str, candidate_name: str) -> float:
    a, b = _tokenize(scanned), _tokenize(candidate_name)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def find_existing_food_matches(name: str, brand: Optional[str] = None, limit: int = 5) -> List[Dict]:
    """Searches USDA/CNF for `name`/`brand` and returns candidates scored
    and sorted by token-overlap similarity, weakest matches dropped
    entirely (see MIN_MATCH_SCORE). Each result is a normal search_foods()
    result dict (source, id, name, brand, category, nutrients,
    serving_size, serving_unit) plus a `match_score` field."""
    scanned = f"{brand} {name}".strip() if brand else name
    candidates = search_foods(scanned, ["USDA", "CNF"])

    scored = [{**c, "match_score": round(_token_overlap_score(scanned, c["name"]), 2)} for c in candidates]
    scored = [c for c in scored if c["match_score"] >= MIN_MATCH_SCORE]
    scored.sort(key=lambda c: c["match_score"], reverse=True)
    return scored[:limit]

"""Unit tests for app/food_matching.py's find_existing_food_matches().

Scoring behavior verified against real USDA FoodData Central search
responses for "Kraft Macaroni and Cheese", "Chobani Greek Yogurt Plain",
and "Quaker Oatmeal" before this was written (see PR discussion) --
these fixtures are trimmed copies of those real responses' shape, not
synthetic placeholder data, specifically to pin the token-overlap
scoring behavior that real food names actually exercise (word reordering
between a label's phrasing and USDA's naming convention)."""
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.food_matching import find_existing_food_matches  # noqa: E402


def _food(name, source="USDA", brand=""):
    return {"source": source, "id": "1", "name": name, "brand": brand, "category": None, "nutrients": {}, "serving_size": 100, "serving_unit": "g"}


def test_exact_match_ranks_first(monkeypatch):
    monkeypatch.setattr(
        "app.food_matching.search_foods",
        lambda query, sources: [
            _food("KRAFT VELVEETA Pasteurized Process Cheese Spread"),
            _food("APPLEBEE'S, KRAFT, Macaroni & Cheese, from kid's menu"),
            _food("KRAFT, MACARONI & CHEESE DINNER", brand="KRAFT"),
        ],
    )
    matches = find_existing_food_matches("Macaroni and Cheese", "Kraft")
    assert matches[0]["name"] == "KRAFT, MACARONI & CHEESE DINNER"
    assert matches[0]["match_score"] > matches[1]["match_score"]


def test_word_reordering_does_not_tank_the_right_match(monkeypatch):
    # USDA's naming convention reverses word order relative to how a
    # label/brand name naturally reads -- a sequence-similarity scorer
    # (difflib.SequenceMatcher) ranks "Blueberry" above the correct
    # "plain" match here; token-overlap must not.
    monkeypatch.setattr(
        "app.food_matching.search_foods",
        lambda query, sources: [
            _food("Yogurt, Greek, Blueberry, CHOBANI"),
            _food("Yogurt, Greek, nonfat, plain, CHOBANI"),
        ],
    )
    matches = find_existing_food_matches("Greek Yogurt Plain", "Chobani")
    assert matches[0]["name"] == "Yogurt, Greek, nonfat, plain, CHOBANI"


def test_weak_candidates_are_dropped_entirely(monkeypatch):
    monkeypatch.setattr(
        "app.food_matching.search_foods",
        lambda query, sources: [_food("Totally Unrelated Food Product")],
    )
    matches = find_existing_food_matches("Macaroni and Cheese", "Kraft")
    assert matches == []


def test_generic_single_word_with_no_brand_does_not_overconfidently_match(monkeypatch):
    # "Oatmeal" alone against a 5-word branded candidate has genuinely low
    # token overlap (1/5) -- correctly filtered rather than confidently
    # linking to the first thing that happens to share one common word.
    monkeypatch.setattr(
        "app.food_matching.search_foods",
        lambda query, sources: [_food("Cereals, QUAKER, QUAKER MultiGrain Oatmeal, dry")],
    )
    matches = find_existing_food_matches("Oatmeal")
    assert matches == []


def test_name_and_brand_together_matches_the_same_candidate(monkeypatch):
    monkeypatch.setattr(
        "app.food_matching.search_foods",
        lambda query, sources: [_food("Cereals, QUAKER, QUAKER MultiGrain Oatmeal, dry")],
    )
    matches = find_existing_food_matches("MultiGrain Oatmeal", "Quaker")
    assert len(matches) == 1


def test_results_are_capped_at_limit(monkeypatch):
    monkeypatch.setattr(
        "app.food_matching.search_foods",
        lambda query, sources: [_food(f"Kraft Macaroni and Cheese variant {i}") for i in range(10)],
    )
    matches = find_existing_food_matches("Macaroni and Cheese", "Kraft", limit=3)
    assert len(matches) == 3

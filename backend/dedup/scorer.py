"""
Weighted evidence scorer for cross-source listing deduplication.
Each signal outputs a dissimilarity score (0 = identical, 1 = different).
Weighted sum determines tier: auto-merge / review / distinct.
"""
from typing import Optional, Dict, Tuple
from backend.dedup.adjacency import location_dissimilarity


# --- Weights (must sum to 1.0) ---
WEIGHTS = {
    "image": 0.30,
    "price": 0.25,
    "bedrooms": 0.20,
    "baths": 0.05,
    "location": 0.20,
}

# --- Tier thresholds (dissimilarity: lower = more similar) ---
TIER_AUTO_MERGE = 0.36
TIER_REVIEW = 0.50
# Above TIER_REVIEW = distinct


def _score_image(distance: int) -> float:
    """Scale triple-hash min distance 0-8 to 0.0-1.0."""
    return min(distance, 8) / 8.0


def _score_price(a: Optional[float], b: Optional[float]) -> float:
    """Percentage gap between two prices, capped at 20% -> 1.0."""
    if a is None or b is None:
        return 0.5
    if a == 0 and b == 0:
        return 0.0
    max_price = max(a, b)
    if max_price == 0:
        return 0.5
    gap = abs(a - b) / max_price
    return min(gap / 0.20, 1.0)


def _score_bedrooms(a: Optional[int], b: Optional[int]) -> float:
    """Bedroom difference scaled: 0->0.0, 1->0.33, 2->0.67, 3+->1.0."""
    if a is None or b is None:
        return 0.3
    diff = abs(a - b)
    return min(diff, 3) / 3.0


def _score_baths(a: Optional[float], b: Optional[float]) -> float:
    """Bath difference scaled same as bedrooms. Null = neutral (0.0)."""
    if a is None or b is None:
        return 0.0
    diff = abs(a - b)
    return min(diff, 3) / 3.0


def score_pair(
    phash_distance: int,
    price_a: Optional[float],
    price_b: Optional[float],
    beds_a: Optional[int],
    beds_b: Optional[int],
    baths_a: Optional[float],
    baths_b: Optional[float],
    location_a: Optional[str],
    location_b: Optional[str],
) -> Tuple[float, str, Dict[str, float]]:
    """
    Score a candidate pair. Returns:
      (total_score, tier, {signal: individual_score})
    """
    signals = {
        "image": _score_image(phash_distance),
        "price": _score_price(price_a, price_b),
        "bedrooms": _score_bedrooms(beds_a, beds_b),
        "baths": _score_baths(baths_a, baths_b),
        "location": location_dissimilarity(location_a, location_b),
    }

    total = sum(WEIGHTS[k] * signals[k] for k in WEIGHTS)

    if total < TIER_AUTO_MERGE:
        tier = "auto-merge"
    elif total <= TIER_REVIEW:
        tier = "review"
    else:
        tier = "distinct"
    
    # Location gate: non-adjacent pairs can't auto-merge
    if signals["location"] >= 0.5 and tier == "auto-merge":
        tier = "review"

    # Beds gate: a CONFIRMED bed-count mismatch can't auto-merge.
    # Units in one complex share marketing photos (img 0.0) and similar
    # prices, so a 1-bed vs 2-bed pair can slip under the threshold —
    # but different bed counts are never the same unit. Found during
    # rent triage 2026-07-16 (three same-complex pairs at 0.16-0.19).
    # Null bed counts score exactly 0.3 and do NOT trigger this gate.
    if signals["bedrooms"] > 0.3 and tier == "auto-merge":
        tier = "review"

    return total, tier, signals

"""
Weighted evidence scorer for cross-source listing deduplication.
Each signal outputs a dissimilarity score (0 = identical, 1 = different).
Weighted sum determines tier: auto-merge / review / distinct.
"""
from typing import Optional, Dict, Tuple


# --- Weights (must sum to 1.0) ---
WEIGHTS = {
    "phash": 0.35,
    "price": 0.25,
    "bedrooms": 0.20,
    "baths": 0.10,
    "location": 0.10,
}

# --- Tier thresholds (dissimilarity: lower = more similar) ---
TIER_AUTO_MERGE = 0.36
TIER_REVIEW = 0.50
# Above TIER_REVIEW = distinct


# --- Suburb adjacency map ---
# Each entry is a set of suburbs considered adjacent.
# A suburb is adjacent to itself and to everything in its group.
ADJACENCY_GROUPS = [
    {"lavington", "kilimani"},
    {"westlands", "loresho", "mountain view"},
    {"karen", "lang'ata", "langata", "hardy", "karen hardy", "miotoni road", "kerarapon road", "kcb leadership"},
    {"runda", "gigiri", "kiambu road"},
    {"garden estate", "thome", "ridgeways", "ridgeway"},
    {"kileleshwa", "riverside"},
    {"south c", "mombasa rd"},
    {"kitengela road", "langata", "lang'ata"},
]

# Build a lookup: suburb -> set of all adjacent suburbs (including itself)
_ADJACENCY_MAP: Dict[str, set] = {}
for group in ADJACENCY_GROUPS:
    for suburb in group:
        _ADJACENCY_MAP.setdefault(suburb, set()).update(group)


def _score_phash(distance: int) -> float:
    """Scale Hamming distance 0-10 to 0.0-1.0."""
    return min(distance, 10) / 10.0


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


def _score_location(a: Optional[str], b: Optional[str]) -> float:
    """
    Same suburb -> 0.0, adjacent -> 0.3, non-adjacent -> 0.7.
    Null -> 0.3 (same as adjacent = mild uncertainty).
    """
    if a is None or b is None:
        return 0.3
    a_lower = a.strip().lower()
    b_lower = b.strip().lower()
    if a_lower == b_lower:
        return 0.0
    # Check adjacency
    adjacent_to_a = _ADJACENCY_MAP.get(a_lower, set())
    if b_lower in adjacent_to_a:
        return 0.3
    return 0.7


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
        "phash": _score_phash(phash_distance),
        "price": _score_price(price_a, price_b),
        "bedrooms": _score_bedrooms(beds_a, beds_b),
        "baths": _score_baths(baths_a, baths_b),
        "location": _score_location(location_a, location_b),
    }

    total = sum(WEIGHTS[k] * signals[k] for k in WEIGHTS)

    if total < TIER_AUTO_MERGE:
        tier = "auto-merge"
    elif total <= TIER_REVIEW:
        tier = "review"
    else:
        tier = "distinct"

    return total, tier, signals
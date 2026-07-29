from datetime import datetime, timezone
from typing import Optional

from backend.db.client import get_client
import hashlib

# Plausible price bands per category, in KES. Outside these a "price" is a
# portal data error, not a bargain — agents typo, paste sale prices into
# rent listings, and post bait figures. Bounds were set from the observed
# tails, not intuition:
#   rent  <3,000    120 listings, KSh 0-2,900. Not a monthly rent.
#         >5m       167 listings, up to KSh 300,000,000/month. The
#                   1m-5m band above the cut is only 13 listings and IS
#                   plausible for serviced penthouses, so it stays.
#   sale  <500,000  141 listings. Under ~USD 3,800 for a Nairobi
#                   property is not credible. 500k-2m (21) is cheap but
#                   possible, so it stays.
# Junk prices are treated exactly like POA — NULL, never 0 (standing
# rule). The listing survives and still links out; we just decline to
# publish a number we don't believe.
PRICE_BOUNDS = {
    "rent": (3_000, 5_000_000),
    "sale": (500_000, 20_000_000_000),
}


def sane_price(price: Optional[float], category: str) -> Optional[float]:
    """Implausible prices become NULL. Returns None for None."""
    if price is None:
        return None
    lo, hi = PRICE_BOUNDS.get(category, (0, float("inf")))
    if price < lo or price > hi:
        return None
    return float(price)


def compute_cluster_key(listing) -> str:
    parts = [
        listing.image_urls[0] if listing.image_urls else "",
        str(listing.bedrooms) if listing.bedrooms is not None else "",
        f"{listing.baths:.1f}" if listing.baths is not None else "",
        f"{listing.size_sqm:.2f}" if listing.size_sqm is not None else "",
        f"{listing.price:.2f}" if listing.price is not None else "",
    ]
    joined = "|".join(parts)
    return hashlib.md5(joined.encode()).hexdigest()

def save_listings(listings: list, source_id: int, category: str = "sale") -> None:
    db = get_client()
    rows = []
    for listing in listings:
        # Sanitise BEFORE compute_cluster_key: the key hashes the price,
        # so it must hash the value we actually store, or the key stops
        # describing the row it belongs to.
        listing.price = sane_price(listing.price, category)
        rows.append({
            "source_id": source_id,
            "category": category,
            "source_listing_id": listing.source_listing_id,
            "url": listing.url,
            "title": listing.title,
            "price": listing.price,
            "location": listing.location,
            "bedrooms": listing.bedrooms,
            "baths": listing.baths,
            "size_sqm": listing.size_sqm,
            "price_currency": "KES",
            "last_seen": datetime.now(timezone.utc).isoformat(),
            # Re-seen listings flip back to active (stale detection in
            # scripts/refresh.py marks the ones that stop appearing).
            "status": "active",
            "image_url": listing.image_urls[0] if listing.image_urls else None,
            "address": listing.address,
            "raw_json": listing.raw,
            "cluster_key": compute_cluster_key(listing),
        })
    if rows:
        db.table("listings").upsert(rows, on_conflict="source_id,source_listing_id").execute()
from datetime import datetime, timezone
from backend.db.client import get_client
import hashlib

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
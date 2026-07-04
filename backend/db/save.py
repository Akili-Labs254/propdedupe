from datetime import datetime, timezone
from backend.db.client import get_client
from backend.scraper.base import ScrapedListing

def save_listings(listings: list, source_id: int) -> None:
    db = get_client()

    for listing in listings:
        row = {
            "source_id": source_id,
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
            "raw_json": listing.raw,
        }
        db.table("listings").upsert(row, on_conflict="source_id,source_listing_id").execute()
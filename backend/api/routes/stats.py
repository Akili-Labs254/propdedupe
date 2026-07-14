"""Pipeline statistics endpoint."""
from fastapi import APIRouter
from backend.db.client import get_client


router = APIRouter(tags=["Stats"])


@router.get("/stats")
def get_stats():
    """Headline numbers for the dedup pipeline."""
    db = get_client()

    listings = db.table("listings").select("id", count="exact").execute()
    properties = db.table("properties").select("id", count="exact").execute()
    matches = db.table("matches").select("id", count="exact").execute()

    total_listings = listings.count
    total_properties = properties.count
    total_matches = matches.count

    dedup_rate = round(
        (1 - total_properties / total_listings) * 100, 1
    ) if total_listings else 0

    return {
        "total_listings": total_listings,
        "total_properties": total_properties,
        "total_matches": total_matches,
        "dedup_rate_pct": dedup_rate,
    }
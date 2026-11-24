"""Pipeline statistics endpoint."""
from fastapi import APIRouter
from backend.db.client import get_client


router = APIRouter(tags=["Stats"])


@router.get("/stats")
def get_stats():
    """Headline numbers for the dedup pipeline."""
    db = get_client()

    # .limit(1): we only want the count. Without it each call also pulled
    # up to 1,000 rows from tables of 60k-100k, and the endpoint stopped
    # answering on the free tier.
    def count(table, active_only=False):
        q = db.table(table).select("id", count="exact")
        if active_only:
            q = q.eq("status", "active")
        return q.limit(1).execute().count or 0

    total_listings = count("listings")
    total_properties = count("properties")
    total_matches = count("matches")
    active_listings = count("listings", active_only=True)
    active_properties = count("properties", active_only=True)

    def rate(listings, properties):
        return round((1 - properties / listings) * 100, 1) if listings else 0

    return {
        # Every row ever scraped, any status: "how much duplication has the
        # engine ever resolved". Kept for compatibility.
        "total_listings": total_listings,
        "total_properties": total_properties,
        "total_matches": total_matches,
        "dedup_rate_pct": rate(total_listings, total_properties),
        # What the site serves today: the figure to quote publicly.
        "active_listings": active_listings,
        "active_properties": active_properties,
        "active_dedup_rate_pct": rate(active_listings, active_properties),
    }
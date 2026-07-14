"""Review queue endpoints — admin-only match review and resolution."""
import os
from typing import Optional
from fastapi import APIRouter, Query, HTTPException, Header
from backend.db.client import get_client

router = APIRouter(prefix="/review", tags=["Review Queue"])

ADMIN_KEY = os.getenv("ADMIN_API_KEY")


def _require_admin(x_admin_key: Optional[str] = Header(None)):
    """Check the X-Admin-Key header matches the secret in .env."""
    if not ADMIN_KEY or x_admin_key != ADMIN_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing admin key")


@router.get("")
def list_review_pairs(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
):
    """List match pairs in the review queue — pairs the scorer flagged as
    possibly the same property but wasn't confident enough to auto-merge."""
    db = get_client()

    # Fetch review-tier matches with pagination
    offset = (page - 1) * per_page
    result = (db.table("matches")
              .select("*", count="exact")
              .eq("tier", "review")
              .order("score")
              .range(offset, offset + per_page - 1)
              .execute())

    # Collect all listing IDs to fetch details in one batch
    listing_ids = set()
    for match in result.data:
        listing_ids.add(match["listing_a_id"])
        listing_ids.add(match["listing_b_id"])

    # Fetch listing details
    listings = {}
    if listing_ids:
        rows = (db.table("listings")
                .select("id, source_id, title, price, location, bedrooms, "
                        "baths, image_url, url")
                .in_("id", list(listing_ids))
                .execute())
        for row in rows.data:
            listings[row["id"]] = row

    # Enrich each match with listing details
    enriched = []
    for match in result.data:
        enriched.append({
            **match,
            "listing_a": listings.get(match["listing_a_id"]),
            "listing_b": listings.get(match["listing_b_id"]),
        })

    return {
        "page": page,
        "per_page": per_page,
        "total": result.count,
        "data": enriched,
    }


@router.post("/{match_id}/merge")
def merge_pair(match_id: int, x_admin_key: Optional[str] = Header(None)):
    """Merge a review pair — move all listings from property B to property A,
    delete property B, mark the match as resolved."""
    _require_admin(x_admin_key)
    db = get_client()

    # Fetch the match
    match = (db.table("matches")
             .select("*")
             .eq("id", match_id)
             .execute())

    if not match.data:
        raise HTTPException(status_code=404, detail="Match not found")

    match = match.data[0]
    if match["tier"] != "review":
        raise HTTPException(status_code=400, detail="Match is not in review tier")

    # Find which properties these listings belong to
    listing_a = (db.table("listings")
                 .select("property_id")
                 .eq("id", match["listing_a_id"])
                 .execute()).data[0]
    listing_b = (db.table("listings")
                 .select("property_id")
                 .eq("id", match["listing_b_id"])
                 .execute()).data[0]

    keep_pid = listing_a["property_id"]
    remove_pid = listing_b["property_id"]

    if keep_pid == remove_pid:
        # Already the same property — just update the match tier
        db.table("matches").update({"tier": "merged"}).eq("id", match_id).execute()
        return {"detail": "Listings already share a property", "property_id": keep_pid}

    # Move all listings from property B to property A
    db.table("listings") \
        .update({"property_id": keep_pid}) \
        .eq("property_id", remove_pid) \
        .execute()

    # Update source_count on the kept property
    sources = (db.table("listings")
               .select("source_id")
               .eq("property_id", keep_pid)
               .execute())
    distinct_sources = len(set(r["source_id"] for r in sources.data))
    db.table("properties") \
        .update({"source_count": distinct_sources}) \
        .eq("id", keep_pid) \
        .execute()

    # Delete the empty property
    db.table("properties").delete().eq("id", remove_pid).execute()

    # Mark the match as merged
    db.table("matches").update({"tier": "merged"}).eq("id", match_id).execute()

    return {"detail": "Merged", "property_id": keep_pid}


@router.post("/{match_id}/dismiss")
def dismiss_pair(match_id: int, x_admin_key: Optional[str] = Header(None)):
    """Dismiss a review pair — mark as not a match so it won't reappear."""
    _require_admin(x_admin_key)
    db = get_client()

    match = (db.table("matches")
             .select("*")
             .eq("id", match_id)
             .execute())

    if not match.data:
        raise HTTPException(status_code=404, detail="Match not found")

    if match.data[0]["tier"] != "review":
        raise HTTPException(status_code=400, detail="Match is not in review tier")

    db.table("matches").update({"tier": "dismissed"}).eq("id", match_id).execute()

    return {"detail": "Dismissed"}
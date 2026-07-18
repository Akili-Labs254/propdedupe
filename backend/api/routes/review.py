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


def _record_decision(db, match: dict, decision: str):
    """Persist the human decision keyed by the STABLE listing pair.

    Match rows are wiped on every dedup rebuild; review_decisions rows
    survive, and persist_dedup reapplies them each run.
    """
    db.table("review_decisions").upsert({
        "listing_a_id": match["listing_a_id"],
        "listing_b_id": match["listing_b_id"],
        "decision": decision,
    }, on_conflict="listing_a_id,listing_b_id").execute()


@router.get("")
def list_review_pairs(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    scope: Optional[str] = Query(None, description="'cross' or 'intra'"),
    category: Optional[str] = Query(None, description="'sale' or 'rent'"),
):
    """List match pairs in the review queue — pairs the scorer flagged as
    possibly the same property but wasn't confident enough to auto-merge."""
    db = get_client()
    return fetch_review_page(db, page, per_page, scope, category)


def fetch_review_page(db, page: int, per_page: int,
                      scope: Optional[str] = None,
                      category: Optional[str] = None) -> dict:
    """Review-tier matches enriched with both listings' details.
    Shared by the JSON API and the admin HTML page."""
    offset = (page - 1) * per_page
    query = (db.table("matches")
             .select("*", count="exact")
             .eq("tier", "review"))
    if scope in ("cross", "intra"):
        query = query.eq("scope", scope)
    if category in ("sale", "rent"):
        query = query.eq("category", category)
    result = (query.order("score")
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
                        "baths, image_url, url, status")
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


@router.post("/merge-properties")
def merge_properties(
    slug_keep: str = Query(..., description="Slug of the property to keep"),
    slug_remove: str = Query(..., description="Slug of the duplicate to fold in"),
    x_admin_key: Optional[str] = Header(None),
):
    """Manually merge two properties that sneaked past the dedup engine
    (e.g. same-source re-lists with different photos, which cluster_key
    and cross-source phash blocking both miss).

    Durable: records a merge decision on a representative listing pair,
    so the dedup rebuild re-merges them every run. Immediate: moves the
    listings, redirects the removed slug, recomputes the kept property's
    aggregates, deletes the duplicate row.
    """
    _require_admin(x_admin_key)
    db = get_client()

    props = (db.table("properties")
             .select("id, slug")
             .in_("slug", [slug_keep, slug_remove])
             .execute())
    by_slug = {r["slug"]: r["id"] for r in props.data}
    if slug_keep not in by_slug or slug_remove not in by_slug:
        missing = [s for s in (slug_keep, slug_remove) if s not in by_slug]
        raise HTTPException(status_code=404, detail=f"Not found: {missing}")
    keep_pid, remove_pid = by_slug[slug_keep], by_slug[slug_remove]
    if keep_pid == remove_pid:
        raise HTTPException(status_code=400, detail="Same property")

    # Representative listing pair (smallest id each side) → durable decision
    reps = []
    for pid in (keep_pid, remove_pid):
        r = (db.table("listings").select("id")
             .eq("property_id", pid).order("id").limit(1).execute())
        if not r.data:
            raise HTTPException(status_code=400,
                                detail=f"Property {pid} has no listings")
        reps.append(r.data[0]["id"])
    _record_decision(db, {"listing_a_id": min(reps),
                          "listing_b_id": max(reps)}, "merge")

    # Redirect the removed slug — it may already be indexed
    db.table("property_slugs").upsert(
        {"slug": slug_remove, "target_slug": slug_keep},
        on_conflict="slug").execute()

    # Move listings, then recompute aggregates on the kept property
    db.table("listings") \
        .update({"property_id": keep_pid}) \
        .eq("property_id", remove_pid) \
        .execute()

    members = (db.table("listings")
               .select("source_id, price, status, first_seen")
               .eq("property_id", keep_pid)
               .execute()).data
    prices = [float(m["price"]) for m in members if m.get("price")]
    firsts = [m["first_seen"] for m in members if m.get("first_seen")]
    db.table("properties").update({
        "source_count": len(set(m["source_id"] for m in members)),
        "price_min": min(prices) if prices else None,
        "price_max": max(prices) if prices else None,
        "status": "active" if any(m["status"] == "active" for m in members)
                  else "inactive",
        "first_seen": min(firsts) if firsts else None,
    }).eq("id", keep_pid).execute()

    db.table("properties").delete().eq("id", remove_pid).execute()

    return {"detail": "Merged", "kept": slug_keep,
            "listings_moved": len(members)}


@router.post("/{match_id}/merge")
def merge_pair(match_id: int, x_admin_key: Optional[str] = Header(None)):
    """Merge a review pair — move all listings from property B to property A,
    delete property B, mark the match as resolved."""
    _require_admin(x_admin_key)
    db = get_client()
    return do_merge(db, match_id)


def do_merge(db, match_id: int) -> dict:
    """Core merge logic — shared by the JSON API and the admin page.
    Caller is responsible for auth."""
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

    _record_decision(db, match, "merge")

    if keep_pid is None or remove_pid is None:
        # Mid-rebuild: property links are cleared and not yet rebuilt.
        # The decision above IS recorded and will apply on the next
        # rebuild — only the immediate merge is skipped.
        raise HTTPException(
            status_code=409,
            detail="Listing not linked to a property (rebuild in "
                   "progress?). Decision saved; it applies at the next "
                   "rebuild.")

    if keep_pid == remove_pid:
        # Already the same property — just update the match tier
        db.table("matches").update({"tier": "merged"}).eq("id", match_id).execute()
        return {"detail": "Listings already share a property", "property_id": keep_pid}

    # Redirect the removed property's slug before deleting it — a merged
    # property's URL may already be indexed and must not 404.
    slugs = (db.table("properties")
             .select("id, slug")
             .in_("id", [keep_pid, remove_pid])
             .execute())
    slug_by_pid = {r["id"]: r["slug"] for r in slugs.data}
    if slug_by_pid.get(remove_pid) and slug_by_pid.get(keep_pid):
        db.table("property_slugs").upsert({
            "slug": slug_by_pid[remove_pid],
            "target_slug": slug_by_pid[keep_pid],
        }, on_conflict="slug").execute()

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
    return do_dismiss(db, match_id)


def do_dismiss(db, match_id: int) -> dict:
    """Core dismiss logic — shared by the JSON API and the admin page.
    Caller is responsible for auth."""
    match = (db.table("matches")
             .select("*")
             .eq("id", match_id)
             .execute())

    if not match.data:
        raise HTTPException(status_code=404, detail="Match not found")

    if match.data[0]["tier"] != "review":
        raise HTTPException(status_code=400, detail="Match is not in review tier")

    _record_decision(db, match.data[0], "dismiss")
    db.table("matches").update({"tier": "dismissed"}).eq("id", match_id).execute()

    return {"detail": "Dismissed"}
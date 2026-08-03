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

def _existing_categories(db, source_id: int, ids: list) -> dict:
    """{source_listing_id: category} for ids already stored.

    Chunked at 100 to stay inside Supabase's URL length limit — same
    reason fetch_listing_details chunks.
    """
    found = {}
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        if not chunk:
            continue
        res = (db.table("listings")
               .select("source_listing_id, category")
               .eq("source_id", source_id)
               .in_("source_listing_id", chunk)
               .execute())
        for row in res.data:
            found[str(row["source_listing_id"])] = row["category"]
    return found


def save_listings(listings: list, source_id: int, category: str = "sale") -> None:
    db = get_client()

    # category is a HARD partition (standing rule), but the upsert
    # conflict key is (source_id, source_listing_id) and does NOT include
    # category — so a listing surfacing under the wrong category scrape
    # would silently overwrite the stored row's category AND price. That
    # is the shape of the price_history corruption found 2026-08: rent
    # rows receiving sale-magnitude prices, minute after minute.
    #
    # P24 encodes the category in the URL (-to-rent-in- / -for-sale-in-),
    # so the same id appearing under both is a scrape fault, not a genuine
    # relisting. Refuse the write and say so loudly rather than corrupting
    # a partition the whole dedup pipeline depends on.
    ids = [str(l.source_listing_id) for l in listings if l.source_listing_id]
    existing = _existing_categories(db, source_id, ids)

    rows = []
    conflicts = []
    for listing in listings:
        sid = str(listing.source_listing_id)
        prior = existing.get(sid)
        if prior and prior != category:
            conflicts.append((sid, prior))
            continue
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
    if conflicts:
        for sid, prior in conflicts[:10]:
            print(f"  WARNING: skipped listing {sid} — stored category "
                  f"{prior!r}, this scrape says {category!r}. Category is a "
                  "hard partition; refusing to overwrite.")
        if len(conflicts) > 10:
            print(f"  ... and {len(conflicts) - 10} more category conflicts")
        print(f"  {len(conflicts)} listing(s) skipped on category conflict — "
              "if this is more than a handful, the scrape URLs are wrong")

    if rows:
        db.table("listings").upsert(rows, on_conflict="source_id,source_listing_id").execute()
from datetime import datetime, timezone
from typing import Optional

from backend.db.client import get_client, with_retry
from backend.scraper.base import ParserOutOfDate
import hashlib

# Plausible price bands per category, in KES. Outside these a "price" is a
# portal data error, not a bargain: agents typo, paste sale prices into
# rent listings, and post bait figures. Bounds were set from the observed
# tails, not intuition:
#   rent  <3,000    120 listings, KSh 0-2,900. Not a monthly rent.
#         >5m       167 listings, up to KSh 300,000,000/month. The
#                   1m-5m band above the cut is only 13 listings and IS
#                   plausible for serviced penthouses, so it stays.
#   sale  <500,000  141 listings. Under ~USD 3,800 for a Nairobi
#                   property is not credible. 500k-2m (21) is cheap but
#                   possible, so it stays.
# Junk prices are treated exactly like POA: NULL, never 0 (standing
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


# Floor area bounds, m². Same treatment as price: a parse error is not a
# very small flat. Set from the observed tails across 18,081 active
# listings carrying a size:
#   <10     418 rows, smallest 1 m². Not a dwelling.
#   20-25   189 rows: a real Nairobi bedsitter, so the floor sits below.
#   500-1k  796 rows: real, a large Karen or Runda house.
#   >2000   175 rows, largest 542,331 m² (54 hectares). Plot sizes and
#           typos, not floor area.
# Unlike price this needs no per-category split: a house is a house.
SIZE_BOUNDS = (10.0, 2000.0)


def sane_size(size_sqm: Optional[float]) -> Optional[float]:
    """Implausible floor areas become NULL. Returns None for None."""
    if size_sqm is None:
        return None
    lo, hi = SIZE_BOUNDS
    if size_sqm < lo or size_sqm > hi:
        return None
    return float(size_sqm)


def compute_cluster_key(listing) -> Optional[str]:
    """Exact-duplicate key: same cover image URL, beds, baths, size, price.

    None when there is no image. The key exists to catch the same advert
    re-posted, and the shared image URL is what makes that evidence. Without
    it the key is just "|2|2.0||85000.00", shared by every 2-bed 2-bath
    KSh 85,000 flat in Nairobi, and persist_dedup's step 2 would merge them
    all into one property. 24 Nov 2026: an image-parser break left 26,008
    listings without images, which would have produced 2,992 false clusters
    covering 21,909 listings (the largest, 155 unrelated rentals) on the
    next rebuild.
    """
    if not listing.image_urls or not listing.image_urls[0]:
        return None
    parts = [
        listing.image_urls[0],
        str(listing.bedrooms) if listing.bedrooms is not None else "",
        f"{listing.baths:.1f}" if listing.baths is not None else "",
        f"{listing.size_sqm:.2f}" if listing.size_sqm is not None else "",
        f"{listing.price:.2f}" if listing.price is not None else "",
    ]
    joined = "|".join(parts)
    return hashlib.md5(joined.encode()).hexdigest()

# Fields a listing never legitimately loses once a source has shown them.
# If a scrape comes back without one, the parser missed it: keep the stored
# value rather than writing NULL over it. Price is deliberately NOT here: an
# advert can genuinely switch to "Price on application", so a missing price
# is only treated as a fault when a whole page loses it (see below).
#
# Size is NOT sticky either: the parser returns None on purpose for acres
# and out-of-bounds figures, and stickiness would keep an old wrong value
# ("102 120 m²" stored as 102) forever.
STICKY_FIELDS = ("url", "title", "location", "address", "image_url",
                 "bedrooms", "baths")

# Fields watched for a page-wide collapse. Both P24 incidents (prices in
# August, images in November) were one selector silently matching nothing:
# every listing still parsed, and the upsert wrote NULL over the stored value
# for every listing the crawl touched. Stickiness hides that for the sticky
# fields; this turns it into an error before anything is written.
WATCHED_FIELDS = ("price", "image_url", "title", "location", "bedrooms", "url")
COLLAPSE_MIN_ROWS = 3       # never judge on fewer stored rows than this
COLLAPSE_SHARE = 0.25       # ...or on less than this share of them


class FieldCollapse(ParserOutOfDate):
    """A field that the stored rows have came back empty across a page."""


def _existing_rows(db, source_id: int, ids: list) -> dict:
    """{source_listing_id: stored row} for ids already stored.

    Carries category (the partition guard) plus every field the merge and
    the collapse check need. Chunked at 100 to stay inside Supabase's URL
    length limit: same reason fetch_listing_details chunks.
    """
    cols = ", ".join(("source_listing_id", "category", "price")
                     + STICKY_FIELDS)
    found = {}
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        if not chunk:
            continue
        res = with_retry(lambda chunk=chunk: db.table("listings")
                         .select(cols)
                         .eq("source_id", source_id)
                         .in_("source_listing_id", chunk)
                         .execute())
        for row in res.data:
            found[str(row["source_listing_id"])] = row
    return found


def check_field_collapse(pairs) -> None:
    """Raise FieldCollapse if a watched field vanished across the page.

    pairs: [(incoming_row, stored_row)] for listings already in the DB,
    with incoming values BEFORE the sticky merge. A field "vanished" for a
    listing when the stored row has it and the scrape does not.
    """
    if len(pairs) < COLLAPSE_MIN_ROWS:
        return
    threshold = max(COLLAPSE_MIN_ROWS, COLLAPSE_SHARE * len(pairs))
    for field in WATCHED_FIELDS:
        lost = sum(1 for new, old in pairs
                   if old.get(field) is not None and new.get(field) is None)
        if lost >= threshold:
            raise FieldCollapse(
                f"{lost} of {len(pairs)} already-stored listings on this page "
                f"came back without {field!r}. The source markup has probably "
                "changed; refusing to write NULL over the stored values.")


def merge_sticky(row: dict, stored: Optional[dict]) -> dict:
    """Keep stored values for sticky fields the scrape came back without."""
    if not stored:
        return row
    for field in STICKY_FIELDS:
        if row.get(field) is None and stored.get(field) is not None:
            row[field] = stored[field]
    return row


def save_listings(listings: list, source_id: int, category: str = "sale") -> None:
    db = get_client()

    # No id, no row: the upsert key is (source_id, source_listing_id), the
    # column is NOT NULL, and one bad row fails the whole page's batch.
    no_id = [l for l in listings if not l.source_listing_id]
    if no_id:
        print(f"  WARNING: {len(no_id)} listing(s) with no source id skipped.")
    # One row per id. Postgres refuses an upsert batch that touches the same
    # row twice ("ON CONFLICT DO UPDATE command cannot affect row a second
    # time"), which would fail the page. Last occurrence wins.
    by_id = {}
    for l in listings:
        if l.source_listing_id:
            by_id[str(l.source_listing_id)] = l
    listings = list(by_id.values())

    ids = list(by_id)
    existing = _existing_rows(db, source_id, ids)

    rows = []
    conflicts = []
    compared = []    # (incoming, stored) pairs for the collapse check
    for listing in listings:
        sid = str(listing.source_listing_id)
        stored = existing.get(sid)
        # category is a HARD partition (standing rule), but the upsert
        # conflict key is (source_id, source_listing_id) and does NOT include
        # category, so a listing surfacing under the wrong category scrape
        # would silently overwrite the stored row's category AND price. That
        # is the shape of the price_history corruption found 2026-08: rent
        # rows receiving sale-magnitude prices, minute after minute.
        #
        # P24 encodes the category in the URL (-to-rent-in- / -for-sale-in-),
        # so the same id appearing under both is a scrape fault, not a genuine
        # relisting. Refuse the write and say so loudly rather than corrupting
        # a partition the whole dedup pipeline depends on.
        prior = stored.get("category") if stored else None
        if prior and prior != category:
            conflicts.append((sid, prior))
            continue
        listing.price = sane_price(listing.price, category)
        listing.size_sqm = sane_size(listing.size_sqm)
        row = {
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
        }
        if stored:
            compared.append((dict(row), stored))
            merge_sticky(row, stored)
        # The key must hash the values we actually store (sanitised AND
        # merged), or it stops describing the row it belongs to.
        listing.image_urls = [row["image_url"]] if row["image_url"] else []
        for f in ("bedrooms", "baths", "size_sqm"):
            setattr(listing, f, row[f])
        row["cluster_key"] = compute_cluster_key(listing)
        rows.append(row)

    # Before ANY write: a page-wide collapse means the parser broke.
    check_field_collapse(compared)

    if conflicts:
        # One conflict per page is expected and not a fault: P24 slots a
        # promoted rental into every sale results page. 651 pages on the
        # 2026-08-12 run produced exactly 651 conflicts, never two on a
        # page. Protecting price_history from that is the whole point of
        # the guard, so say so rather than crying wolf 651 times.
        if len(conflicts) == 1:
            sid, prior = conflicts[0]
            print(f"  1 listing skipped ({sid}: stored {prior!r}, scrape says "
                  f"{category!r}) — expected, promoted cross-category slot.")
        else:
            for sid, prior in conflicts[:10]:
                print(f"  WARNING: skipped listing {sid} — stored category "
                      f"{prior!r}, this scrape says {category!r}. Category is "
                      "a hard partition; refusing to overwrite.")
            if len(conflicts) > 10:
                print(f"  ... and {len(conflicts) - 10} more category conflicts")
            print(f"  WARNING: {len(conflicts)} category conflicts on one page "
                  "— more than the single promoted slot P24 injects. Check "
                  "the scrape URLs.")

    if rows:
        # Idempotent (on_conflict), so safe to retry after a lost response.
        with_retry(lambda: db.table("listings")
                   .upsert(rows, on_conflict="source_id,source_listing_id")
                   .execute())
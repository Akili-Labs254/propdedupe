"""Repair BRK locations polluted by the 2026-09-02 headline-split bug.

The card parser split on "|", agent headlines contain pipes, so the title
lookup missed and the fallback swallowed the headline as the location. 663
active BRK listings ended up with marketing copy in `location`, and since
`location` feeds suburb assignment, `properties.suburb` now contains whole
paragraphs like:

    "kes 350 000 per month why this spring valley furnished three-bedroom
     apartment for rent is an excellent choice located in the serene..."

Ten properties share that suburb. MIN_SEGMENT is 5, so it generates an
indexable segment page titled with a marketing paragraph. That is the part
that needs fixing quickly: thin content that reaches Google is far harder
to undo than a bad database row.

The parser is already fixed, but BuyRentKenya closed to crawlers on 2 Sep
(ADR 0010), so these rows can never be corrected by re-scraping. They have
to be repaired in place.

Every affected row carries its slug, and the slug holds the area:

    2-bedroom-apartment-for-sale-westlands-area-3771447 -> "Westlands Area"

Read-only unless --apply is passed. Chunked, per the standing rule.

    python -m scripts.repair_brk_locations           # dry run
    python -m scripts.repair_brk_locations --apply
"""

import argparse
from typing import Dict, List, Optional

from backend.db.client import get_client
from backend.scraper.buyrentkenya import _clean_location, _location_from_slug

BRK_SOURCE_ID = 1
FETCH_BATCH = 1000
WRITE_CHUNK = 200


def is_polluted(location: Optional[str]) -> bool:
    """A location the fixed parser would have rejected."""
    if not location:
        return False
    return _clean_location(location) != location.strip()


def slug_of(row: dict) -> Optional[str]:
    raw = row.get("raw_json") or {}
    slug = raw.get("slug") if isinstance(raw, dict) else None
    if slug:
        return slug
    url = row.get("url") or ""
    return url.rstrip("/").split("/")[-1] or None


def fetch_candidates(db) -> List[dict]:
    rows: List[dict] = []
    offset = 0
    while True:
        batch = (db.table("listings")
                 .select("id, location, url, raw_json")
                 .eq("source_id", BRK_SOURCE_ID)
                 .eq("status", "active")
                 .order("id")
                 .range(offset, offset + FETCH_BATCH - 1)
                 .execute())
        if not batch.data:
            break
        rows.extend(batch.data)
        if len(batch.data) < FETCH_BATCH:
            break
        offset += FETCH_BATCH
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="write the repairs (default is a dry run)")
    ap.add_argument("--limit", type=int, default=15,
                    help="examples to print (default 15)")
    args = ap.parse_args()

    db = get_client(timeout=600)

    print("Fetching active BRK listings...")
    rows = fetch_candidates(db)
    print(f"  {len(rows)} fetched")

    repairs: Dict[int, str] = {}
    unrepairable: List[int] = []

    for row in rows:
        if not is_polluted(row.get("location")):
            continue
        slug = slug_of(row)
        derived = _clean_location(_location_from_slug(slug)) if slug else None
        if derived:
            repairs[row["id"]] = derived
        else:
            unrepairable.append(row["id"])

    print(f"\n  polluted:     {len(repairs) + len(unrepairable)}")
    print(f"  repairable:   {len(repairs)}")
    print(f"  unrepairable: {len(unrepairable)}")

    shown = 0
    for row in rows:
        if row["id"] in repairs and shown < args.limit:
            before = (row["location"] or "")[:60]
            print(f"\n  {row['id']}")
            print(f"    before: {before}...")
            print(f"    after:  {repairs[row['id']]}")
            shown += 1

    if not args.apply:
        print("\nDry run. Re-run with --apply to write.")
        return

    ids = sorted(repairs)
    for i in range(0, len(ids), WRITE_CHUNK):
        chunk = ids[i:i + WRITE_CHUNK]
        for listing_id in chunk:
            (db.table("listings")
               .update({"location": repairs[listing_id]})
               .eq("id", listing_id)
               .execute())
        print(f"  {min(i + WRITE_CHUNK, len(ids))}/{len(ids)} repaired...")

    print("\nDone. Run persist_dedup to re-derive properties.suburb — the "
          "bad suburbs live on properties, not listings, and only a rebuild "
          "clears them.")


if __name__ == "__main__":
    main()

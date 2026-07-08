"""
Fetch BRK detail pages to backfill baths and url from JSON-LD.
Each detail page has an Accommodation node (numberOfBathroomsTotal)
and an Offer node (url).
"""
import json
import time
import random
import requests
from bs4 import BeautifulSoup
from backend.db.client import get_client


BASE = "https://www.buyrentkenya.com/listings"


def fetch_detail_fields(source_listing_id: str):
    """
    Fetch a BRK detail page, parse its JSON-LD, return
    (baths, url) or (None, None) on failure.
    """
    resp = requests.get(
        f"{BASE}/{source_listing_id}",
        allow_redirects=True,
        timeout=15,
    )
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    block = soup.find("script", type="application/ld+json")
    if not block:
        return None, None

    data = json.loads(block.string)
    graph = data.get("@graph", [])

    baths = None
    url = None

    for node in graph:
        ntype = node.get("@type")

        if ntype == "Accommodation":
            raw_baths = node.get("numberOfBathroomsTotal")
            if raw_baths is not None:
                try:
                    baths = float(raw_baths)
                except (ValueError, TypeError):
                    pass

        if ntype == "Offer":
            url = node.get("url")

    return baths, url


def backfill():
    db = get_client()

    # Fetch all BRK listing IDs that are missing baths
    rows = []
    start = 0
    batch_size = 1000
    while True:
        result = (
            db.table("listings")
            .select("id, source_listing_id")
            .eq("source_id", 1)
            .is_("baths", "null")
            .order("id")
            .range(start, start + batch_size - 1)
            .execute()
        )
        if not result.data:
            break
        rows.extend(result.data)
        start += batch_size

    print(f"Fetching details for {len(rows)} BRK listings...")

    success = 0
    failed = 0
    for i, row in enumerate(rows, 1):
        try:
            baths, url = fetch_detail_fields(row["source_listing_id"])
            update = {}
            if baths is not None:
                update["baths"] = baths
            if url is not None:
                update["url"] = url
            if update:
                db.table("listings").update(update).eq("id", row["id"]).execute()
                success += 1
            else:
                print(f"  No data found for {row['source_listing_id']}")
        except Exception as e:
            print(f"  Failed {row['source_listing_id']}: {e}")
            failed += 1

        if i % 50 == 0:
            print(f"  {i}/{len(rows)} done ({success} updated, {failed} failed)")

        # Polite crawl delay: 1-2s random
        time.sleep(random.uniform(1, 2))

    print(f"\nDone. {success} updated, {failed} failed out of {len(rows)} total.")


if __name__ == "__main__":
    backfill()
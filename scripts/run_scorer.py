"""
Run the weighted evidence scorer across all cross-source
pHash candidates and output tier classifications.
"""
from typing import Dict, List
from backend.db.client import get_client
from backend.dedup.scorer import score_pair
from scripts.cross_source_match import (
    fetch_distinct_hashes,
    find_cross_matches,
    hex_to_int,
    hamming_distance,
)


def fetch_listing_details(db, ids: List[int]) -> Dict[int, dict]:
    """Fetch listing fields needed for scoring, keyed by id."""
    details = {}
    # Batch fetch in chunks of 100 (Supabase URL length limit)
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        id_str = ",".join(str(x) for x in chunk)
        result = (
            db.table("listings")
            .select("id, price, bedrooms, baths, location")
            .in_("id", chunk)
            .execute()
        )
        for row in result.data:
            details[row["id"]] = row
    return details


def main():
    db = get_client()

    print("Fetching hashes...")
    brk_hashes = fetch_distinct_hashes(db, 1)
    p24_hashes = fetch_distinct_hashes(db, 2)

    print("Finding candidates (threshold <= 10)...")
    matches = find_cross_matches(brk_hashes, p24_hashes, threshold=10)
    print(f"  {len(matches)} candidate pairs")

    # Collect all listing IDs we need details for
    all_ids = set()
    for _, _, _, brk_ids, p24_ids in matches:
        all_ids.update(brk_ids)
        all_ids.update(p24_ids)

    print(f"Fetching details for {len(all_ids)} listings...")
    details = fetch_listing_details(db, list(all_ids))

    # Score each candidate pair
    results = []
    for brk_hex, p24_hex, dist, brk_ids, p24_ids in matches:
        # Use first listing from each side as representative
        brk_row = details.get(brk_ids[0], {})
        p24_row = details.get(p24_ids[0], {})

        total, tier, signals = score_pair(
            phash_distance=dist,
            price_a=float(brk_row["price"]) if brk_row.get("price") else None,
            price_b=float(p24_row["price"]) if p24_row.get("price") else None,
            beds_a=brk_row.get("bedrooms"),
            beds_b=p24_row.get("bedrooms"),
            baths_a=float(brk_row["baths"]) if brk_row.get("baths") else None,
            baths_b=float(p24_row["baths"]) if p24_row.get("baths") else None,
            location_a=brk_row.get("location"),
            location_b=p24_row.get("location"),
        )

        results.append({
            "brk_ids": brk_ids,
            "p24_ids": p24_ids,
            "phash_dist": dist,
            "total": total,
            "tier": tier,
            "signals": signals,
        })

    # Sort by score (best matches first)
    results.sort(key=lambda r: r["total"])

    # Print results by tier
    tiers = {"auto-merge": [], "review": [], "distinct": []}
    for r in results:
        tiers[r["tier"]].append(r)

    for tier_name in ["auto-merge", "review", "distinct"]:
        tier_list = tiers[tier_name]
        print(f"\n=== {tier_name.upper()} ({len(tier_list)} pairs) ===")
        for r in tier_list:
            brk = details.get(r["brk_ids"][0], {})
            p24 = details.get(r["p24_ids"][0], {})
            print(f"  score={r['total']:.3f}  dist={r['phash_dist']}"
                  f"  BRK: {brk.get('bedrooms')}bed {brk.get('location')} "
                  f"KSh{brk.get('price')}"
                  f"  P24: {p24.get('bedrooms')}bed {p24.get('location')} "
                  f"KSh{p24.get('price')}")
            print(f"    signals: {r['signals']}")
            print(f"    ids: BRK={r['brk_ids']} P24={r['p24_ids']}")

    print(f"\nSummary: {len(tiers['auto-merge'])} auto-merge, "
          f"{len(tiers['review'])} review, {len(tiers['distinct'])} distinct")


if __name__ == "__main__":
    main()
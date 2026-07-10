"""
Persist dedup pipeline results to the database.

Runs the same blocking + scoring pipeline as run_scorer.py, then:
1. Inserts all scored pairs into the `matches` table
2. Groups auto-merge pairs into connected components (one per property)
3. Creates a `properties` row for each group
4. Sets `property_id` on every listing in each group

Run: python -u -m scripts.persist_dedup 2>&1 | tee data/persist_dedup.log
Re-runnable: clears previous results before inserting (matches table
truncated, property_id reset to NULL, properties table cleared).
"""
from typing import Dict, List, Set, Optional
from backend.db.client import get_client
from backend.dedup.scorer import score_pair
from scripts.cross_source_match import (
    fetch_distinct_hashes,
    find_cross_matches,
)


def fetch_listing_details(db, ids: List[int]) -> Dict[int, dict]:
    """Fetch listing fields needed for scoring + property creation, keyed by id.

    Batches in chunks of 100 to stay within Supabase URL length limits.
    """
    details = {}
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        result = (
            db.table("listings")
            .select("id, source_id, title, price, bedrooms, baths, "
                    "location, size_sqm, image_url")
            .in_("id", chunk)
            .execute()
        )
        for row in result.data:
            details[row["id"]] = row
    return details


# ── Union-Find ──────────────────────────────────────────────────────
# Groups listing IDs into connected components. If A matches B and
# B matches C, all three end up in one group — one property.
#
# HOW IT WORKS: each ID starts as its own "parent." When two IDs are
# linked (union), one becomes the parent of the other. find() walks
# up the parent chain to the root. Two IDs are in the same group if
# they share a root. Path compression (flattening the chain on each
# find) keeps it fast.

class UnionFind:
    def __init__(self):
        self._parent = {}

    def find(self, x: int) -> int:
        """Find the root representative of x's group."""
        if x not in self._parent:
            self._parent[x] = x
        # Path compression: point each node directly at the root
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def union(self, a: int, b: int):
        """Merge the groups containing a and b."""
        root_a = self.find(a)
        root_b = self.find(b)
        if root_a != root_b:
            self._parent[root_b] = root_a

    def groups(self) -> Dict[int, List[int]]:
        """Return all groups as {root_id: [member_ids]}."""
        result = {}
        for x in self._parent:
            root = self.find(x)
            result.setdefault(root, []).append(x)
        return result


def pick_primary(listing_ids: List[int], details: Dict[int, dict]) -> dict:
    """Pick the best listing to represent a property group.

    Preference: BRK listings first (richer titles like "5 Bed Villa
    with En Suite in Karen" vs P24's "5 Bedroom House"), then by most
    non-null fields.

    Returns the full detail dict for the chosen listing.
    """
    candidates = [details[lid] for lid in listing_ids if lid in details]
    if not candidates:
        return {}

    def sort_key(row):
        # BRK = source_id 1 → sort first (lower number)
        source_priority = row.get("source_id", 99)
        # Count non-null fields (more data = better representative)
        filled = sum(1 for v in row.values() if v is not None)
        # Negate filled so higher count sorts first
        return (source_priority, -filled)

    candidates.sort(key=sort_key)
    return candidates[0]


def clear_previous_results(db):
    """Remove previous dedup results so the script is re-runnable.

    Order matters: clear FKs before the rows they point to.
    """
    print("Clearing previous results...")

    # 1. Unlink listings from properties
    #    Supabase client doesn't support UPDATE...SET without a filter,
    #    so we filter for non-null property_id
    db.table("listings") \
        .update({"property_id": None}) \
        .not_.is_("property_id", "null") \
        .execute()

    # 2. Clear matches table
    #    Delete all rows — gt(id, 0) matches everything since IDs are positive
    db.table("matches") \
        .delete() \
        .gt("id", 0) \
        .execute()

    # 3. Clear properties table
    db.table("properties") \
        .delete() \
        .gt("id", 0) \
        .execute()

    print("  Done — tables cleared.")


def main():
    db = get_client()

    clear_previous_results(db)

    # ── Run the pipeline (same as run_scorer.py) ─────────────────
    print("\nFetching hashes...")
    brk_hashes = fetch_distinct_hashes(db, 1)
    p24_hashes = fetch_distinct_hashes(db, 2)

    print("Finding candidates (threshold <= 8)...")
    matches = find_cross_matches(brk_hashes, p24_hashes, threshold=8)
    print(f"  {len(matches)} candidate pairs")

    # Collect all listing IDs
    all_ids = set()
    for _, _, _, brk_ids, p24_ids in matches:
        all_ids.update(brk_ids)
        all_ids.update(p24_ids)

    print(f"Fetching details for {len(all_ids)} listings...")
    details = fetch_listing_details(db, list(all_ids))

    # ── Score every pair ─────────────────────────────────────────
    print("Scoring pairs...")
    scored = []
    for brk_hex, p24_hex, dist, brk_ids, p24_ids in matches:
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

        scored.append({
            "brk_ids": brk_ids,
            "p24_ids": p24_ids,
            "brk_rep": brk_ids[0],   # representative ID for this side
            "p24_rep": p24_ids[0],
            "dist": dist,
            "total": total,
            "tier": tier,
            "signals": signals,
        })

    # Count tiers
    tier_counts = {}
    for s in scored:
        tier_counts[s["tier"]] = tier_counts.get(s["tier"], 0) + 1
    print(f"  Tiers: {tier_counts}")

    # ── 1. Insert matches ────────────────────────────────────────
    print("\nInserting matches...")
    match_rows = []
    for s in scored:
        # Enforce listing_a_id < listing_b_id (DB constraint)
        a_id = min(s["brk_rep"], s["p24_rep"])
        b_id = max(s["brk_rep"], s["p24_rep"])

        match_rows.append({
            "listing_a_id": a_id,
            "listing_b_id": b_id,
            "score": round(s["total"], 4),
            "tier": s["tier"],
            "image_score": round(s["signals"]["image"], 2),
            "price_score": round(s["signals"]["price"], 2),
            "beds_score": round(s["signals"]["bedrooms"], 2),
            "baths_score": round(s["signals"]["baths"], 2),
            "location_score": round(s["signals"]["location"], 2),
        })

    # Batch insert in chunks (Supabase payload size limit)
    for i in range(0, len(match_rows), 50):
        chunk = match_rows[i:i + 50]
        db.table("matches").insert(chunk).execute()
    print(f"  Inserted {len(match_rows)} match rows.")

    # ── 2. Group auto-merge pairs into properties ────────────────
    uf = UnionFind()
    auto_merge_pairs = [s for s in scored if s["tier"] == "auto-merge"]

    for s in auto_merge_pairs:
        # Link all IDs in this pair together
        all_pair_ids = s["brk_ids"] + s["p24_ids"]
        first = all_pair_ids[0]
        for lid in all_pair_ids[1:]:
            uf.union(first, lid)

    groups = uf.groups()
    print(f"\n{len(groups)} property groups from {len(auto_merge_pairs)} auto-merge pairs")

    # ── 3. Create property rows and link listings ────────────────
    print("Creating properties and linking listings...")
    created = 0

    for root_id, member_ids in groups.items():
        primary = pick_primary(member_ids, details)
        if not primary:
            print(f"  WARNING: no details for group {member_ids}, skipping")
            continue

        # Count distinct sources in this group
        source_ids = set()
        for lid in member_ids:
            row = details.get(lid)
            if row:
                source_ids.add(row.get("source_id"))

        # Insert property row
        prop_data = {
            "title": primary.get("title"),
            "price": float(primary["price"]) if primary.get("price") else None,
            "location": primary.get("location"),
            "bedrooms": primary.get("bedrooms"),
            "bathrooms": float(primary["baths"]) if primary.get("baths") else None,
            "size_sqm": float(primary["size_sqm"]) if primary.get("size_sqm") else None,
            "image_url": primary.get("image_url"),
            "source_count": len(source_ids),
        }
        result = db.table("properties").insert(prop_data).execute()
        property_id = result.data[0]["id"]

        # Link all member listings to this property
        for lid in member_ids:
            db.table("listings") \
                .update({"property_id": property_id}) \
                .eq("id", lid) \
                .execute()

        created += 1

    print(f"  Created {created} properties, linked {sum(len(v) for v in groups.values())} listings.")

    # ── Summary ──────────────────────────────────────────────────
    print(f"\n=== Done ===")
    print(f"  Matches inserted: {len(match_rows)}")
    print(f"  Properties created: {created}")
    print(f"  Tiers: {tier_counts}")


if __name__ == "__main__":
    main()

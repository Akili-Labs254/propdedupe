"""
Persist dedup pipeline results to the database.

Runs the same blocking + scoring pipeline as run_scorer.py, then:
  Step 1: Cross-source — scores pairs, inserts matches, groups auto-merge
          pairs via Union-Find into properties, links member listings.
  Step 2: Intra-P24 — collapses cluster_key duplicate groups into properties.
  Step 3: Singletons — every still-unlinked listing becomes its own property
          (previously manual SQL, now automated below).

Run: caffeinate -i python -u -m scripts.persist_dedup 2>&1 | tee data/persist_dedup.log
Re-runnable: clears previous results before inserting.
"""
import sys
import time
from typing import Dict, List, Set, Optional
from collections import defaultdict

import httpx

from backend.db.client import get_client
from backend.dedup.scorer import score_pair
from backend.dedup.slugs import SlugAllocator
from backend.dedup.adjacency import normalize_suburb
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
            .select("id, source_id, source_listing_id, status, first_seen, "
                    "cluster_key, category, title, price, bedrooms, baths, "
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

    # These are big bulk writes: on a loaded free-tier box they can run
    # longer than the HTTP client's read timeout even though Postgres
    # completes them server-side. _with_retry re-issues on that timeout —
    # the retry then finds the work already done (or a now-tiny remainder)
    # and returns fast. Same pattern as every other hot call here.

    # 1. Unlink listings from properties
    #    Supabase client doesn't support UPDATE...SET without a filter,
    #    so we filter for non-null property_id
    _with_retry(lambda: db.table("listings")
                .update({"property_id": None})
                .not_.is_("property_id", "null")
                .execute())

    # 2. Clear matches table
    #    Delete all rows — gt(id, 0) matches everything since IDs are positive
    _with_retry(lambda: db.table("matches")
                .delete()
                .gt("id", 0)
                .execute())

    # 3. Clear properties table
    _with_retry(lambda: db.table("properties")
                .delete()
                .gt("id", 0)
                .execute())

    print("  Done — tables cleared.")


def snapshot_slug_state(db) -> Dict[int, str]:
    """Capture {listing_id: current slug} BEFORE the rebuild clears
    everything — this is what lets slugs survive the rebuild."""
    pid_to_slug = {}
    offset = 0
    while True:
        batch = (db.table("properties").select("id, slug")
                 .not_.is_("slug", "null")
                 .order("id").range(offset, offset + 999).execute())
        if not batch.data:
            break
        for row in batch.data:
            pid_to_slug[row["id"]] = row["slug"]
        if len(batch.data) < 1000:
            break
        offset += 1000

    snapshot = {}
    offset = 0
    while True:
        batch = (db.table("listings").select("id, property_id")
                 .not_.is_("property_id", "null")
                 .order("id").range(offset, offset + 999).execute())
        if not batch.data:
            break
        for row in batch.data:
            slug = pid_to_slug.get(row["property_id"])
            if slug:
                snapshot[row["id"]] = slug
        if len(batch.data) < 1000:
            break
        offset += 1000
    return snapshot


def persist_slug_snapshot(db, snapshot: Dict[int, str]):
    """Write the snapshot to the slug_snapshot table BEFORE clearing —
    a crash between clear and rebuild must not lose slug inheritance."""
    _with_retry(lambda: db.table("slug_snapshot")
                .delete().gt("listing_id", 0).execute())
    rows = [{"listing_id": lid, "slug": slug}
            for lid, slug in snapshot.items()]
    for i in range(0, len(rows), 500):
        chunk = rows[i:i + 500]
        _with_retry(lambda c=chunk:
                    db.table("slug_snapshot").insert(c).execute())


def load_slug_snapshot_fallback(db) -> Dict[int, str]:
    """Recover the last crash-safe snapshot (rerun-after-crash path)."""
    out = {}
    offset = 0
    while True:
        batch = (db.table("slug_snapshot").select("listing_id, slug")
                 .order("listing_id").range(offset, offset + 999).execute())
        if not batch.data:
            break
        for row in batch.data:
            out[row["listing_id"]] = row["slug"]
        if len(batch.data) < 1000:
            break
        offset += 1000
    return out


def property_status(member_rows: List[dict]) -> str:
    """active if ANY member listing is still active, else inactive."""
    return "active" if any(r.get("status") == "active" for r in member_rows) \
        else "inactive"


def suburb_of(row: dict):
    """Canonical suburb for segment pages; None when location missing."""
    loc = row.get("location")
    return normalize_suburb(loc) if loc else None


def earliest_first_seen(member_rows: List[dict]):
    """Property crawl stamp = when its earliest member was first scraped.
    ISO-8601 strings in a uniform format compare correctly as strings."""
    vals = [r.get("first_seen") for r in member_rows if r.get("first_seen")]
    return min(vals) if vals else None


def price_range(member_rows: List[dict]) -> dict:
    """Min/max price across member listings — sources sometimes disagree
    on price, and the range is information only an aggregator has."""
    prices = [float(r["price"]) for r in member_rows if r.get("price")]
    if not prices:
        return {"price_min": None, "price_max": None}
    return {"price_min": min(prices), "price_max": max(prices)}


def beds_baths_range(member_rows: List[dict]) -> dict:
    """Min/max bed and bath counts across member listings — sources
    sometimes disagree (a DSQ / domestic-staff room counted or not), and
    the range is information only an aggregator has. Display only: search
    still filters on the property's primary bedrooms/bathrooms, same as
    price_min/max vs price."""
    beds = [int(r["bedrooms"]) for r in member_rows if r.get("bedrooms")]
    baths = [float(r["baths"]) for r in member_rows if r.get("baths")]
    return {
        "beds_min": min(beds) if beds else None,
        "beds_max": max(beds) if beds else None,
        "baths_min": min(baths) if baths else None,
        "baths_max": max(baths) if baths else None,
    }


def link_batch(db, pairs: List[dict]):
    """Bulk listing→property linking via the link_listings RPC
    (one round-trip per chunk instead of one per listing)."""
    for i in range(0, len(pairs), 500):
        chunk = pairs[i:i + 500]
        _with_retry(lambda c=chunk: db.rpc("link_listings", {"pairs": c}).execute())


def _with_retry(make_call, attempts=4):
    """Run a Supabase call, retrying on transient network errors.

    Supabase's proxy closes an HTTP/2 connection after ~10k requests
    (GOAWAY); the in-flight request dies with RemoteProtocolError. The
    next attempt opens a fresh connection, so a plain retry with
    backoff fixes it. make_call must BUILD AND EXECUTE the request
    (a lambda), so each retry issues a fresh request.
    """
    for attempt in range(attempts):
        try:
            return make_call()
        except (httpx.HTTPError, ConnectionError) as e:
            if attempt == attempts - 1:
                raise
            wait = 2 ** attempt
            print(f"  transient network error ({type(e).__name__}), "
                  f"retrying in {wait}s...")
            time.sleep(wait)


def create_singleton_properties(db, alloc: SlugAllocator,
                                prop_members: Optional[Dict[int, List[int]]] = None) -> int:
    """Step 3: give every still-unlinked listing its own property row.

    Paginates through property_id IS NULL listings, inserts property rows
    in batches (with slug + status), then bulk-links via the
    link_listings RPC. Supabase returns inserted rows in payload order,
    so index i of the insert result corresponds to index i of the batch.
    """
    BATCH_SIZE = 100
    created = 0

    while True:
        # Always fetch offset 0: linking removes rows from this filter,
        # so the next "first page" is always the next unprocessed batch.
        batch = _with_retry(lambda: (
            db.table("listings")
            .select("id, source_id, source_listing_id, status, first_seen, "
                    "category, title, price, location, bedrooms, baths, "
                    "size_sqm, image_url")
            .is_("property_id", "null")
            .order("id")
            .range(0, BATCH_SIZE - 1)
            .execute()))
        if not batch.data:
            break

        prop_rows = []
        for row in batch.data:
            prop_rows.append({
                "title": row.get("title"),
                "price": float(row["price"]) if row.get("price") else None,
                "location": row.get("location"),
                "bedrooms": row.get("bedrooms"),
                "bathrooms": float(row["baths"]) if row.get("baths") else None,
                "size_sqm": float(row["size_sqm"]) if row.get("size_sqm") else None,
                "image_url": row.get("image_url"),
                "source_count": 1,
                "slug": alloc.slug_for([row]),
                "status": property_status([row]),
                "first_seen": row.get("first_seen"),
                "suburb": suburb_of(row),
                "category": row.get("category") or "sale",
                **price_range([row]),
                **beds_baths_range([row]),
            })

        result = _with_retry(
            lambda: db.table("properties").insert(prop_rows).execute())

        pairs = []
        for listing_row, prop_row in zip(batch.data, result.data):
            pairs.append({"listing_id": listing_row["id"],
                          "property_id": prop_row["id"]})
            if prop_members is not None:
                prop_members[prop_row["id"]] = [listing_row["id"]]
        link_batch(db, pairs)

        created += len(batch.data)
        if created % 1000 == 0:
            print(f"  {created} singletons done...")

    return created


def main():
    db = get_client()

    # Snapshot slug state BEFORE clearing — rebuilt properties inherit
    # the slugs their member listings carried (stable public URLs).
    print("Snapshotting slug state...")
    snapshot = snapshot_slug_state(db)
    if snapshot:
        try:
            persist_slug_snapshot(db, snapshot)
            print(f"  {len(snapshot)} listings carry a slug "
                  "(crash-safe copy saved)")
        except Exception as e:
            print(f"  {len(snapshot)} listings carry a slug "
                  f"(crash-safe copy FAILED: {e} — run "
                  "backend/db/slug_snapshot.sql)")
    else:
        try:
            snapshot = load_slug_snapshot_fallback(db)
        except Exception:
            snapshot = {}
        if snapshot:
            print(f"  {len(snapshot)} slugs RECOVERED from crash-safe copy")
        else:
            print("  0 slugs found (first run, or crash lost the snapshot "
                  "— slugs will regenerate deterministically)")
    alloc = SlugAllocator(snapshot)
    prop_members: Dict[int, List[int]] = {}  # new property_id → member ids

    clear_previous_results(db)

    # ── Run the pipeline (same as run_scorer.py) ─────────────────
    # Blocking is partitioned by category: sale and rent never match.
    print("\nFetching hashes...")
    hashes_by_cat = {}
    matches = []
    for cat in ("sale", "rent"):
        bh = fetch_distinct_hashes(db, 1, category=cat)
        ph = fetch_distinct_hashes(db, 2, category=cat)
        hashes_by_cat[cat] = (bh, ph)
        if bh and ph:
            print(f"  [{cat}] {len(bh)} BRK x {len(ph)} P24 triples — "
                  "finding candidates (threshold <= 8)...")
            matches += find_cross_matches(bh, ph, threshold=8)
    print(f"  {len(matches)} candidate pairs")

    # Collect all listing IDs
    all_ids = set()
    for _, _, _, brk_ids, p24_ids in matches:
        all_ids.update(brk_ids)
        all_ids.update(p24_ids)

    # ── Manual review decisions (durable; reapplied every rebuild) ─
    print("Fetching review decisions...")
    decisions = {}
    offset = 0
    while True:
        batch = (db.table("review_decisions")
                 .select("listing_a_id, listing_b_id, decision")
                 .order("listing_a_id")
                 .range(offset, offset + 999)
                 .execute())
        if not batch.data:
            break
        for row in batch.data:
            decisions[(row["listing_a_id"], row["listing_b_id"])] = row["decision"]
        if len(batch.data) < 1000:
            break
        offset += 1000
    print(f"  {len(decisions)} stored decisions")

    # Merge-decision listings need details even if blocking didn't
    # surface them as candidates this run
    for (a, b), d in decisions.items():
        if d == "merge":
            all_ids.add(a)
            all_ids.add(b)

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

    # Human decisions override the scorer: a dismissed pair is never
    # auto-merged (even at a high score), a merged pair always groups.
    for s in scored:
        key = (min(s["brk_rep"], s["p24_rep"]), max(s["brk_rep"], s["p24_rep"]))
        if key in decisions:
            s["tier"] = "merged" if decisions[key] == "merge" else "dismissed"

    # Count tiers
    tier_counts = {}
    for s in scored:
        tier_counts[s["tier"]] = tier_counts.get(s["tier"], 0) + 1
    print(f"  Tiers: {tier_counts}")

    # ── Step 1: Insert matches ───────────────────────────────────
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
            "scope": "cross",
            "category": (details.get(s["brk_rep"], {}).get("category")
                         or "sale"),
        })

    # Batch insert in chunks (Supabase payload size limit)
    for i in range(0, len(match_rows), 50):
        chunk = match_rows[i:i + 50]
        _with_retry(lambda c=chunk: db.table("matches").insert(c).execute())
    print(f"  Inserted {len(match_rows)} match rows.")

    # ── Step 1.5: Intra-source pairs ─────────────────────────────
    # Same site, same image content, different listing rows (re-uploads
    # cluster_key misses). Auto-merge policy set by measured precision
    # (2026-07-15, ~70-pair hand-audit): score <= 0.10 was 100% true
    # duplicates; false positives only appeared from ~0.15 up and all
    # carried loc >= 0.5 (which score_pair's location gate already
    # demotes). So: auto-merge only below INTRA_AUTO_CUT, everything
    # else in [cut, review-threshold) stays human-reviewed.
    INTRA_AUTO_CUT = 0.10
    print("\n--- Step 1.5: Intra-source candidates ---")
    from scripts.intra_source_match import find_intra_matches

    intra_rows = []
    intra_tiers = {}
    intra_auto_groups = []   # [(ids_a, ids_b)] to union into properties
    intra_jobs = []
    for cat, (bh, ph) in hashes_by_cat.items():
        intra_jobs.append((1, f"BRK/{cat}", bh))
        intra_jobs.append((2, f"P24/{cat}", ph))
    for sid, name, hashes in intra_jobs:
        if not hashes:
            continue
        cands = find_intra_matches(hashes)
        print(f"  {name}: {len(cands)} raw candidate pairs")

        need = set()
        for _, ids_a, ids_b in cands:
            need.add(ids_a[0])
            need.add(ids_b[0])
        missing = [i for i in need if i not in details]
        if missing:
            details.update(fetch_listing_details(db, missing))

        for dist, ids_a, ids_b in cands:
            ra, rb = details.get(ids_a[0]), details.get(ids_b[0])
            if not ra or not rb:
                continue
            # Same cluster_key → step 2 already collapses this pair
            if ra.get("cluster_key") and \
                    ra.get("cluster_key") == rb.get("cluster_key"):
                continue

            total, tier, signals = score_pair(
                phash_distance=dist,
                price_a=float(ra["price"]) if ra.get("price") else None,
                price_b=float(rb["price"]) if rb.get("price") else None,
                beds_a=ra.get("bedrooms"),
                beds_b=rb.get("bedrooms"),
                baths_a=float(ra["baths"]) if ra.get("baths") else None,
                baths_b=float(rb["baths"]) if rb.get("baths") else None,
                location_a=ra.get("location"),
                location_b=rb.get("location"),
            )

            key = (min(ids_a[0], ids_b[0]), max(ids_a[0], ids_b[0]))
            if key in decisions:
                tier = "merged" if decisions[key] == "merge" else "dismissed"
            elif tier == "auto-merge" and total > INTRA_AUTO_CUT:
                tier = "review"  # measured precision drops above the cut

            if tier in ("auto-merge", "merged"):
                intra_auto_groups.append((list(ids_a), list(ids_b)))

            intra_tiers[tier] = intra_tiers.get(tier, 0) + 1
            intra_rows.append({
                "listing_a_id": key[0],
                "listing_b_id": key[1],
                "score": round(total, 4),
                "tier": tier,
                "image_score": round(signals["image"], 2),
                "price_score": round(signals["price"], 2),
                "beds_score": round(signals["bedrooms"], 2),
                "baths_score": round(signals["baths"], 2),
                "location_score": round(signals["location"], 2),
                "scope": "intra",
                "category": ra.get("category") or "sale",
            })

    for i in range(0, len(intra_rows), 50):
        chunk = intra_rows[i:i + 50]
        _with_retry(lambda c=chunk: db.table("matches").insert(c).execute())
    print(f"  Inserted {len(intra_rows)} intra-source match rows. "
          f"Tiers: {intra_tiers}")

    # ── Step 1b: Group auto-merge pairs into properties ──────────
    uf = UnionFind()
    auto_merge_pairs = [s for s in scored
                        if s["tier"] in ("auto-merge", "merged")]

    for s in auto_merge_pairs:
        # Link all IDs in this pair together
        all_pair_ids = s["brk_ids"] + s["p24_ids"]
        first = all_pair_ids[0]
        for lid in all_pair_ids[1:]:
            uf.union(first, lid)

    # Intra-source auto-merges (score <= INTRA_AUTO_CUT) and reapplied
    # manual merges join the same union-find, so a listing that matches
    # cross-source AND intra-source lands in ONE property
    for ids_a, ids_b in intra_auto_groups:
        all_ids_pair = ids_a + ids_b
        first = all_ids_pair[0]
        for lid in all_ids_pair[1:]:
            uf.union(first, lid)

    # Merge decisions whose pairs weren't candidates this run still apply
    candidate_keys = {
        (min(s["brk_rep"], s["p24_rep"]), max(s["brk_rep"], s["p24_rep"]))
        for s in scored
    }
    for (a, b), d in decisions.items():
        if d == "merge" and (a, b) not in candidate_keys:
            uf.union(a, b)

    groups = uf.groups()
    print(f"\n{len(groups)} property groups from {len(auto_merge_pairs)} auto-merge pairs")

    # Create property rows and link listings
    print("Creating properties and linking listings...")
    step1_created = 0

    step1_pairs = []
    for root_id, member_ids in groups.items():
        primary = pick_primary(member_ids, details)
        if not primary:
            print(f"  WARNING: no details for group {member_ids}, skipping")
            continue

        member_rows = [details[lid] for lid in member_ids if lid in details]

        # Count distinct sources in this group
        source_ids = set()
        for row in member_rows:
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
            "slug": alloc.slug_for(member_rows or [{"id": member_ids[0]}]),
            "status": property_status(member_rows),
            "first_seen": earliest_first_seen(member_rows),
            "suburb": suburb_of(primary),
            "category": primary.get("category") or "sale",
            **price_range(member_rows),
            **beds_baths_range(member_rows),
        }
        result = _with_retry(
            lambda pd=prop_data: db.table("properties").insert(pd).execute())
        property_id = result.data[0]["id"]

        # Queue member listings for bulk linking
        for lid in member_ids:
            step1_pairs.append({"listing_id": lid, "property_id": property_id})
        prop_members[property_id] = list(member_ids)

        step1_created += 1

    link_batch(db, step1_pairs)

    print(f"  Created {step1_created} properties, linked {sum(len(v) for v in groups.values())} listings.")

    # ── Step 2: Intra-P24 cluster_key groups → properties ────────
    print("\n--- Step 2: Intra-P24 cluster_key groups ---")

    # Fetch all P24 listings with a cluster_key (paginated)
    all_p24 = []
    offset = 0
    BATCH_SIZE = 1000
    while True:
        batch = (db.table("listings")
                 .select("id, cluster_key, property_id, source_id, "
                         "source_listing_id, status, first_seen, category, "
                         "title, price, location, bedrooms, baths, size_sqm, "
                         "image_url")
                 .eq("source_id", 2)
                 .not_.is_("cluster_key", "null")
                 .order("id")
                 .range(offset, offset + BATCH_SIZE - 1)
                 .execute())
        if not batch.data:
            break
        all_p24.extend(batch.data)
        if len(batch.data) < BATCH_SIZE:
            break
        offset += BATCH_SIZE

    print(f"  Fetched {len(all_p24)} P24 listings with cluster_key")

    # Group by (cluster_key, category) in Python — the category part is
    # belt-and-braces (identical price across sale/rent is ~impossible,
    # but the partition rule is absolute)
    clusters = defaultdict(list)
    for listing in all_p24:
        clusters[(listing["cluster_key"], listing.get("category"))].append(listing)

    multi_clusters = {k: v for k, v in clusters.items() if len(v) > 1}
    print(f"  {len(multi_clusters)} clusters with >1 listing")

    step2_created = 0
    step2_linked = 0
    step2_joined = 0  # linked to existing cross-source property

    for cluster_key, members in multi_clusters.items():
        # Check if any member already has a property_id from Step 1
        existing_pid = None
        for m in members:
            if m["property_id"] is not None:
                existing_pid = m["property_id"]
                break

        if existing_pid:
            # Link unlinked members in one call
            unlinked_ids = [m["id"] for m in members if m["property_id"] is None]
            if unlinked_ids:
                _with_retry(lambda ids=unlinked_ids, pid=existing_pid: (
                    db.table("listings")
                    .update({"property_id": pid})
                    .in_("id", ids)
                    .execute()))
                step2_linked += len(unlinked_ids)
                step2_joined += len(unlinked_ids)
            prop_members.setdefault(existing_pid, []).extend(
                m["id"] for m in members)
        else:
            # Create a new property from the first member
            rep = members[0]
            prop_data = {
                "title": rep.get("title"),
                "price": float(rep["price"]) if rep.get("price") else None,
                "location": rep.get("location"),
                "bedrooms": rep.get("bedrooms"),
                "bathrooms": float(rep["baths"]) if rep.get("baths") else None,
                "size_sqm": float(rep["size_sqm"]) if rep.get("size_sqm") else None,
                "image_url": rep.get("image_url"),
                "source_count": 1,
                "slug": alloc.slug_for(members),
                "status": property_status(members),
                "first_seen": earliest_first_seen(members),
                "suburb": suburb_of(rep),
                "category": rep.get("category") or "sale",
                **price_range(members),
                **beds_baths_range(members),
            }
            result = _with_retry(
                lambda pd=prop_data: db.table("properties").insert(pd).execute())
            pid = result.data[0]["id"]
            step2_created += 1

            # Link all members in one call
            member_ids = [m["id"] for m in members]
            prop_members[pid] = list(member_ids)
            _with_retry(lambda ids=member_ids, p=pid: (
                db.table("listings")
                .update({"property_id": p})
                .in_("id", ids)
                .execute()))
            step2_linked += len(members)

    print(f"  Created {step2_created} new properties")
    print(f"  Linked {step2_linked} listings ({step2_joined} joined existing cross-source properties)")

    # ── Step 3: Singletons ───────────────────────────────────────
    print("\n--- Step 3: Singleton properties ---")
    step3_created = create_singleton_properties(db, alloc, prop_members)
    print(f"  Created {step3_created} singleton properties")

    # ── Step 4: Retired-slug redirects ───────────────────────────
    # Old slugs nobody inherited 301-redirect to wherever their former
    # member listings ended up (slug → slug, survives future rebuilds).
    print("\n--- Step 4: Retired-slug redirects ---")
    listing_to_new = {lid: pid for pid, lids in prop_members.items()
                      for lid in lids}
    retired = alloc.compute_retired(listing_to_new)

    if retired:
        # Map target property ids → their canonical slugs
        target_pids = list(set(retired.values()))
        pid_to_slug = {}
        for i in range(0, len(target_pids), 100):
            chunk = target_pids[i:i + 100]
            res = _with_retry(lambda c=chunk: (
                db.table("properties").select("id, slug")
                .in_("id", c).execute()))
            for row in res.data:
                pid_to_slug[row["id"]] = row["slug"]

        redirect_rows = [
            {"slug": old, "target_slug": pid_to_slug[pid]}
            for old, pid in retired.items()
            if pid in pid_to_slug and pid_to_slug[pid] != old
        ]
        for i in range(0, len(redirect_rows), 500):
            chunk = redirect_rows[i:i + 500]
            _with_retry(lambda c=chunk: (
                db.table("property_slugs")
                .upsert(c, on_conflict="slug").execute()))
        print(f"  {len(redirect_rows)} retired slugs now redirect")
    else:
        print("  none (first run, or no regroupings)")

    # ── Summary ──────────────────────────────────────────────────
    print(f"\n=== Done (Steps 1 + 2 + 3) ===")
    print(f"  Matches inserted: {len(match_rows)}")
    print(f"  Step 1 — Cross-source properties: {step1_created}")
    print(f"  Step 2 — Cluster properties: {step2_created} ({step2_joined} listings joined existing)")
    print(f"  Step 3 — Singleton properties: {step3_created}")
    print(f"  Total properties: {step1_created + step2_created + step3_created}")
    print(f"  Tiers: {tier_counts}")



if __name__ == "__main__":
    if "--singletons-only" in sys.argv:
        # Resume mode: only create/link singleton properties. Picks up
        # exactly where a crashed run stopped (works off property_id IS
        # NULL), touching nothing already linked. Pre-claims every slug
        # already in the DB so generated slugs can't collide; the
        # pre-crash snapshot is gone, so no inheritance in this mode.
        db = get_client()
        alloc = SlugAllocator({})
        offset = 0
        while True:
            batch = (db.table("properties").select("slug")
                     .not_.is_("slug", "null")
                     .order("id").range(offset, offset + 999).execute())
            if not batch.data:
                break
            alloc._claimed.update(r["slug"] for r in batch.data)
            if len(batch.data) < 1000:
                break
            offset += 1000
        n = create_singleton_properties(db, alloc)
        print(f"Created {n} singleton properties.")
    else:
        main()


"""
Persist dedup pipeline results to the database.

Runs the same blocking + scoring pipeline as run_scorer.py, then:
  Step 1 (cross-source): scores pairs, inserts matches, groups auto-merge
          pairs via Union-Find into properties, links member listings.
  Step 2 (intra-P24): collapses cluster_key duplicate groups into properties.
  Step 3 (singletons): every still-unlinked listing becomes its own property
          (previously manual SQL, now automated below).

Run: caffeinate -i python -u -m scripts.persist_dedup 2>&1 | tee data/persist_dedup.log
Re-runnable: clears previous results before inserting.
"""
import sys
import time
from typing import Dict, List, Set, Optional
from collections import defaultdict

import httpx

from backend.db.client import get_client, with_retry
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
            _with_retry(lambda chunk=chunk: db.table("listings")
            .select("id, source_id, source_listing_id, status, first_seen, "
                    "cluster_key, category, title, price, bedrooms, baths, "
                    "location, size_sqm, image_url")
            .in_("id", chunk)
            .execute())
        )
        for row in result.data:
            details[row["id"]] = row
    return details


# ── Union-Find ──────────────────────────────────────────────────────
# Groups listing IDs into connected components. If A matches B and
# B matches C, all three end up in one group: one property.
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


# Distinct cluster_keys one image may span before we treat it as a stock
# or agency placeholder rather than one flat listed at a few prices. Also
# caps the k_a x k_b pair expansion in intra-source scoring.
CK_GROUP_CAP = 15


def _group_by_cluster_key(ids: List[int], details: Dict[int, dict]
                          ) -> List[List[int]]:
    """Split listing ids into groups that are identical on cluster_key.

    Listings sharing a cluster_key match on image_url + beds + baths +
    size + price, so any member represents the group, which is what lets
    us score one pair per variant instead of one pair per listing.

    Rows with a falsy cluster_key each become their own group: we know
    nothing about them, so we must not silently lump them together.
    """
    groups: Dict[str, List[int]] = {}
    for i in ids:
        row = details.get(i)
        if not row:
            continue
        ck = row.get("cluster_key") or f"__nokey_{i}"
        groups.setdefault(ck, []).append(i)
    return list(groups.values())


CLEAR_CHUNK = 1000  # rows per clear request: small enough that even on a
                    # throttled free-tier box each request finishes well
                    # under Supabase's ~60s API-gateway upstream timeout.


def _unlink_listings_chunked(db):
    """NULL property_id on linked listings, one id-batch at a time.

    A single full-table UPDATE (~48k rows) runs past the API gateway's
    ~60s upstream limit on the free tier and 504s: no client timeout
    helps, the gateway kills it. Batching keeps every request small and
    fast. Each pass grabs the next `CLEAR_CHUNK` still-linked ids; the
    filter shrinks as we null them, so we always fetch fresh rows.
    """
    total = 0
    while True:
        rows = _with_retry(lambda: db.table("listings").select("id")
                           .not_.is_("property_id", "null")
                           .order("id").limit(CLEAR_CHUNK).execute())
        ids = [r["id"] for r in rows.data]
        if not ids:
            break
        _with_retry(lambda ids=ids: db.table("listings")
                    .update({"property_id": None}).in_("id", ids).execute())
        total += len(ids)
    return total


def _clear_table_chunked(db, table):
    """Delete every row from `table` in id-batches (same 60s-gateway
    reason as _unlink_listings_chunked: a full-table DELETE 504s)."""
    total = 0
    while True:
        rows = _with_retry(lambda: db.table(table).select("id")
                           .order("id").limit(CLEAR_CHUNK).execute())
        ids = [r["id"] for r in rows.data]
        if not ids:
            break
        _with_retry(lambda ids=ids: db.table(table)
                    .delete().in_("id", ids).execute())
        total += len(ids)
    return total


def clear_previous_results(db):
    """Remove previous dedup results so the script is re-runnable.

    Order matters: unlink listings (clear the FK) BEFORE deleting the
    properties they point to. All three are chunked: a single bulk
    UPDATE/DELETE on the whole table exceeds Supabase's ~60s API-gateway
    upstream timeout on the free tier and 504s.
    """
    print("Clearing previous results...")
    n = _unlink_listings_chunked(db)          # 1. unlink listings ← properties
    _clear_table_chunked(db, "matches")       # 2. clear matches
    _clear_table_chunked(db, "properties")    # 3. clear properties
    print(f"  Done — tables cleared ({n} listings unlinked).")


def snapshot_slug_state(db) -> Dict[int, str]:
    """Capture {listing_id: current slug} BEFORE the rebuild clears
    everything: this is what lets slugs survive the rebuild."""
    pid_to_slug = {}
    offset = 0
    while True:
        batch = _with_retry(lambda o=offset: db.table("properties")
                            .select("id, slug")
                            .not_.is_("slug", "null")
                            .order("id").range(o, o + 999).execute())
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
        batch = _with_retry(lambda o=offset: db.table("listings")
                            .select("id, property_id")
                            .not_.is_("property_id", "null")
                            .order("id").range(o, o + 999).execute())
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
    """Write the snapshot to the slug_snapshot table BEFORE clearing:
    a crash between clear and rebuild must not lose slug inheritance."""
    _with_retry(lambda: db.table("slug_snapshot")
                .delete().gt("listing_id", 0).execute())
    rows = [{"listing_id": lid, "slug": slug}
            for lid, slug in snapshot.items()]
    for i in range(0, len(rows), 500):
        chunk = rows[i:i + 500]
        _with_retry(lambda c=chunk:
                    db.table("slug_snapshot").insert(c).execute())


# A rebuild that dies partway leaves a partial properties table. Re-running
# then snapshots THAT, and the old code overwrote the good crash-safe copy
# with the degraded one, because it only fell back when the live snapshot was
# completely empty. 2026-08-03 that churned 26,346 public URLs.
#
# So: if live slug state is far below what's already stored, this is a partial
# state, not a shrinking corpus. Keep the stored copy and don't overwrite it.
#
# WHY 0.7 AND NOT 0.5 (the stale guard's number): the snapshot is keyed to
# LISTINGS, and the crash collapsed properties 28,000 -> 6,412 (ratio 0.23)
# while listings-with-a-slug only fell to 27,968 (ratio 0.513), because each
# surviving cluster property still carries many members. A 0.5 threshold sits
# just under the real incident and would not have caught it. Measured, not
# guessed: see tests/test_persist.py.
#
# Erring toward refusing is cheap: a stale snapshot merely remembers slugs for
# listings that may not exist, which SlugAllocator ignores. Erring toward
# overwriting loses public URLs permanently. A legitimate one-run shrink of
# >30% would need a whole source to die, and the stale guard would fire first.
SNAPSHOT_SHRINK_GUARD = 0.7


def stored_snapshot_size(db) -> int:
    """Row count of the crash-safe copy: one request, not a full read."""
    res = _with_retry(lambda: db.table("slug_snapshot")
                      .select("listing_id", count="exact")
                      .limit(1).execute())
    return res.count or 0


def resolve_slug_snapshot(live: Dict[int, str], stored_count: int,
                          load_stored):
    """Decide which slug map to use and whether to persist it.

    Returns (snapshot, should_persist, note). `load_stored` is a callable
    so the decision is testable without a database.
    """
    if not live:
        # Nothing live at all: the pre-existing recovery path.
        stored = load_stored() if stored_count else {}
        if stored:
            return stored, False, f"{len(stored)} slugs RECOVERED from crash-safe copy"
        return {}, False, ("0 slugs found (first run, or crash lost the "
                           "snapshot — slugs will regenerate deterministically)")

    if stored_count and len(live) < stored_count * SNAPSHOT_SHRINK_GUARD:
        stored = load_stored()
        if stored:
            merged = dict(stored)
            merged.update(live)   # live wins where both know a listing
            return merged, False, (
                f"WARNING: live slug state ({len(live)}) is far below the "
                f"stored snapshot ({stored_count}) — treating this as a "
                f"partial/post-crash state. Using {len(merged)} slugs "
                "(stored + live) and NOT overwriting the stored copy.")

    return live, True, f"{len(live)} listings carry a slug"


def load_slug_snapshot_fallback(db) -> Dict[int, str]:
    """Recover the last crash-safe snapshot (rerun-after-crash path)."""
    out = {}
    offset = 0
    while True:
        batch = _with_retry(lambda o=offset: db.table("slug_snapshot")
                            .select("listing_id, slug")
                            .order("listing_id").range(o, o + 999).execute())
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
    """Min/max price across member listings: sources sometimes disagree
    on price, and the range is information only an aggregator has."""
    prices = [float(r["price"]) for r in member_rows if r.get("price")]
    if not prices:
        return {"price_min": None, "price_max": None}
    return {"price_min": min(prices), "price_max": max(prices)}


def beds_baths_range(member_rows: List[dict]) -> dict:
    """Min/max bed and bath counts across member listings: sources
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

    RETRY SAFETY: anything wrapped in this MUST be idempotent. A lost
    response is not a lost write: the server can commit and then have
    the connection die before replying, so the retry re-sends a write
    that already landed. In August 2026 that killed a rebuild 20 minutes in:
    a properties INSERT was retried and hit the unique index on the row
    its own first attempt had just created (23505 on slug). Property
    writes now use upsert(on_conflict="slug") so a replay is a no-op
    update instead of a crash. Keep it that way for anything new.

    EVERY READ IN THIS FILE IS WRAPPED, added 2026-08-27. Reads are
    idempotent by definition, so none of the caution above applies to
    them, and an unwrapped one is a live outage waiting to happen.
    On 26 and 27 Aug the review_decisions fetch died on a dropped TLS
    handshake, twice, each time AFTER clear_previous_results had emptied
    properties and matches. The site served zero listings for 17 hours.
    postgrest's own send_with_retry does not cover connect timeouts.

    If you add a read, wrap it. The cost is one lambda; the cost of not
    doing it is the whole site.
    """
    return with_retry(make_call, attempts)


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
            lambda: db.table("properties")
            .upsert(prop_rows, on_conflict="slug").execute())

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
    db = get_client(timeout=600)  # bulk clears/writes run for minutes on free tier

    # Snapshot slug state BEFORE clearing: rebuilt properties inherit
    # the slugs their member listings carried (stable public URLs).
    print("Snapshotting slug state...")
    live = snapshot_slug_state(db)
    try:
        stored_count = stored_snapshot_size(db)
    except Exception:
        stored_count = 0   # table missing → behave as if first run

    def _load_stored():
        try:
            return load_slug_snapshot_fallback(db)
        except Exception:
            return {}

    snapshot, should_persist, note = resolve_slug_snapshot(
        live, stored_count, _load_stored)
    print(f"  {note}")

    if should_persist:
        try:
            persist_slug_snapshot(db, snapshot)
            print("  crash-safe copy saved")
        except Exception as e:
            print(f"  crash-safe copy FAILED: {e} — run "
                  "backend/db/slug_snapshot.sql")
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
        batch = _with_retry(lambda o=offset: db.table("review_decisions")
                            .select("listing_a_id, listing_b_id, decision")
                            .order("listing_a_id")
                            .range(o, o + 999)
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
    intra_skipped_wide = 0   # candidates dropped by CK_GROUP_CAP
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

        # Every id, not just representatives: cluster_key comes from
        # details and we group both sides by it below.
        need = set()
        for _, ids_a, ids_b in cands:
            need.update(ids_a)
            need.update(ids_b)
        missing = [i for i in need if i not in details]
        if missing:
            details.update(fetch_listing_details(db, missing))

        seen_keys = set()
        for dist, ids_a, ids_b in cands:
            groups_a = _group_by_cluster_key(ids_a, details)
            groups_b = _group_by_cluster_key(ids_b, details)

            # One image spanning many distinct cluster_keys is a stock or
            # agency placeholder, not one flat advertised at a few prices.
            # Skipping caps the k_a x k_b expansion AND stops a shared logo
            # welding unrelated properties together.
            if len(groups_a) > CK_GROUP_CAP or len(groups_b) > CK_GROUP_CAP:
                intra_skipped_wide += 1
                continue

            for ga in groups_a:
                for gb in groups_b:
                    # Identical-image candidates arrive as (ids, ids), so
                    # both sides hold the same groups and the diagonal is
                    # a listing against itself. The cluster_key check
                    # below misses it when cluster_key is falsy, which
                    # produces listing_a_id == listing_b_id and trips the
                    # matches_pair_order constraint.
                    if ga[0] == gb[0]:
                        continue
                    ra, rb = details.get(ga[0]), details.get(gb[0])
                    if not ra or not rb:
                        continue
                    # Same cluster_key → step 2 already collapses this pair
                    if ra.get("cluster_key") and \
                            ra.get("cluster_key") == rb.get("cluster_key"):
                        continue

                    key = (min(ga[0], gb[0]), max(ga[0], gb[0]))
                    if key in seen_keys:
                        continue  # (A,B) and (B,A) both reach here
                    seen_keys.add(key)

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

                    if key in decisions:
                        tier = ("merged" if decisions[key] == "merge"
                                else "dismissed")
                    elif tier == "auto-merge" and total > INTRA_AUTO_CUT:
                        tier = "review"  # precision drops above the cut

                    if tier in ("auto-merge", "merged"):
                        intra_auto_groups.append((list(ga), list(gb)))

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
    if intra_skipped_wide:
        print(f"  {intra_skipped_wide} candidate(s) skipped — image spans "
              f">{CK_GROUP_CAP} cluster_keys (likely a stock/agency image)")

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
        if d != "merge" or (a, b) in candidate_keys:
            continue
        # category is a HARD partition (standing rule) and blocking never
        # produces a cross-category candidate, but this branch unions
        # NON-candidates, so a single bad click in the review UI could
        # otherwise weld a rental onto a for-sale property permanently
        # (decisions are durable and reapplied every rebuild). Refuse it.
        cat_a = (details.get(a) or {}).get("category")
        cat_b = (details.get(b) or {}).get("category")
        if cat_a and cat_b and cat_a != cat_b:
            print(f"  WARNING: skipping cross-category merge decision "
                  f"{a} ({cat_a}) + {b} ({cat_b}) — category is a hard "
                  "partition; delete this review_decisions row")
            continue
        uf.union(a, b)

    groups = uf.groups()
    print(f"\n{len(groups)} property groups from {len(auto_merge_pairs)} auto-merge pairs")

    # Create property rows and link listings
    print("Creating properties and linking listings...")
    step1_created = 0
    step1_multi_source = 0    # groups genuinely spanning >1 site

    step1_pairs = []
    step1_props = []          # property rows to upsert in batches
    step1_slug_members = {}   # slug -> [listing_id], to map ids back
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
        # Queue the property row rather than writing it now. This loop used
        # to upsert one row per group: 4,143 sequential round-trips on the
        # last run. Linking was already batched; the writes weren't.
        if prop_data["slug"] in step1_slug_members:
            print(f"  WARNING: duplicate slug {prop_data['slug']!r} across "
                  "groups — skipping the second. SlugAllocator should "
                  "prevent this.")
            continue
        step1_slug_members[prop_data["slug"]] = list(member_ids)
        step1_props.append(prop_data)
        if len(source_ids) > 1:
            step1_multi_source += 1

    # Batch the writes, mapping ids back BY SLUG: upsert with ON CONFLICT
    # gives no ordering guarantee, and mis-pairing here would attach
    # listings to the wrong property.
    for i in range(0, len(step1_props), 100):
        chunk = step1_props[i:i + 100]
        result = _with_retry(lambda c=chunk: db.table("properties")
                             .upsert(c, on_conflict="slug").execute())
        if len(result.data) != len(chunk):
            print(f"  WARNING: upserted {len(chunk)} rows but got "
                  f"{len(result.data)} back — some groups may be unlinked")
        for row in result.data:
            member_ids = step1_slug_members.get(row["slug"])
            if not member_ids:
                continue
            step1_pairs.extend({"listing_id": lid, "property_id": row["id"]}
                               for lid in member_ids)
            prop_members[row["id"]] = list(member_ids)
            step1_created += 1
        print(f"  {min(i + 100, len(step1_props))}/{len(step1_props)} "
              "group properties written...")

    link_batch(db, step1_pairs)

    print(f"  Created {step1_created} properties, linked {sum(len(v) for v in groups.values())} listings.")

    # ── Step 2: Intra-P24 cluster_key groups → properties ────────
    print("\n--- Step 2: Intra-P24 cluster_key groups ---")

    # Fetch all P24 listings with a cluster_key (paginated)
    all_p24 = []
    offset = 0
    BATCH_SIZE = 1000
    while True:
        batch = _with_retry(lambda o=offset: db.table("listings")
                            .select("id, cluster_key, property_id, source_id, "
                                    "source_listing_id, status, first_seen, "
                                    "category, title, price, location, "
                                    "bedrooms, baths, size_sqm, image_url")
                            .eq("source_id", 2)
                            .not_.is_("cluster_key", "null")
                            .order("id")
                            .range(o, o + BATCH_SIZE - 1)
                            .execute())
        if not batch.data:
            break
        all_p24.extend(batch.data)
        if len(batch.data) < BATCH_SIZE:
            break
        offset += BATCH_SIZE

    print(f"  Fetched {len(all_p24)} P24 listings with cluster_key")

    # Group by (cluster_key, category) in Python: the category part is
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

    # This step used to issue TWO sequential requests per cluster (one
    # property upsert, one listing update) across ~4,500 clusters. That's
    # ~9,000 round-trips against a throttled free-tier box, and it was most
    # of a one-hour rebuild (the singleton step, which batches, ran ~790
    # properties/minute while this crawled). Now: decide everything in
    # memory, then write in batches.

    pairs = []            # {listing_id, property_id} for the link RPC
    new_props = []        # property rows to upsert
    slug_members = {}     # slug -> [listing_id], to map ids back afterwards

    # ── Pass 1: classify every cluster, no I/O ──
    for cluster_key, members in multi_clusters.items():
        existing_pid = next(
            (m["property_id"] for m in members if m["property_id"] is not None),
            None)

        if existing_pid:
            unlinked_ids = [m["id"] for m in members
                            if m["property_id"] is None]
            pairs.extend({"listing_id": lid, "property_id": existing_pid}
                         for lid in unlinked_ids)
            step2_linked += len(unlinked_ids)
            step2_joined += len(unlinked_ids)
            prop_members.setdefault(existing_pid, []).extend(
                m["id"] for m in members)
            continue

        rep = members[0]
        slug = alloc.slug_for(members)
        if slug in slug_members:
            # Two clusters claiming one slug would blow up the batched
            # upsert ("ON CONFLICT DO UPDATE command cannot affect row a
            # second time") and silently merge two properties if it didn't.
            # SlugAllocator should make this impossible: shout, don't guess.
            print(f"  WARNING: duplicate slug {slug!r} across clusters — "
                  "skipping the second. SlugAllocator should prevent this.")
            continue
        slug_members[slug] = [m["id"] for m in members]
        new_props.append({
            "title": rep.get("title"),
            "price": float(rep["price"]) if rep.get("price") else None,
            "location": rep.get("location"),
            "bedrooms": rep.get("bedrooms"),
            "bathrooms": float(rep["baths"]) if rep.get("baths") else None,
            "size_sqm": float(rep["size_sqm"]) if rep.get("size_sqm") else None,
            "image_url": rep.get("image_url"),
            "source_count": 1,
            "slug": slug,
            "status": property_status(members),
            "first_seen": earliest_first_seen(members),
            "suburb": suburb_of(rep),
            "category": rep.get("category") or "sale",
            **price_range(members),
            **beds_baths_range(members),
        })

    # ── Pass 2: batch the property writes ──
    # Map returned ids back BY SLUG, not by position: upsert with ON CONFLICT
    # gives no ordering guarantee, and a silent mis-pairing here would attach
    # listings to the wrong property.
    UPSERT_BATCH = 100
    for i in range(0, len(new_props), UPSERT_BATCH):
        chunk = new_props[i:i + UPSERT_BATCH]
        result = _with_retry(lambda c=chunk: db.table("properties")
                             .upsert(c, on_conflict="slug").execute())
        if len(result.data) != len(chunk):
            print(f"  WARNING: upserted {len(chunk)} rows but got "
                  f"{len(result.data)} back — some clusters may be unlinked")
        for row in result.data:
            member_ids = slug_members.get(row["slug"])
            if not member_ids:
                continue
            prop_members[row["id"]] = list(member_ids)
            pairs.extend({"listing_id": lid, "property_id": row["id"]}
                         for lid in member_ids)
            step2_created += 1
            step2_linked += len(member_ids)
        done = min(i + UPSERT_BATCH, len(new_props))
        print(f"  {done}/{len(new_props)} cluster properties written...")

    # ── Pass 3: one bulk RPC per 500 links ──
    link_batch(db, pairs)

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
    # "Cross-source" named the step, not the outcome: most groups the
    # cross-source stage creates turn out to be single-source after
    # scoring. Report both, because the honest number is the small one
    # and it is the one that gets quoted.
    print(f"  Step 1 — properties created by the cross-source stage: "
          f"{step1_created}")
    print(f"           of which actually span >1 site: {step1_multi_source}")
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
        db = get_client(timeout=600)
        alloc = SlugAllocator({})
        offset = 0
        while True:
            batch = _with_retry(lambda o=offset: db.table("properties")
                                .select("slug")
                                .not_.is_("slug", "null")
                                .order("id").range(o, o + 999).execute())
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


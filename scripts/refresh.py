"""Re-scrape orchestrator: scrape → hash new images → stale detection → dedup.

This is the script the scheduler will eventually run. Each stage is also
runnable on its own for debugging.

Stages (in --all order):
  1. scrape   Both scrapers. save_listings() upserts on (source_id,
              source_listing_id), bumps last_seen, resets status to active.
  2. hashes   Incremental hash backfill — only listings with an image_url
              but no image_phash (i.e. new since last run). NOT the full
              13k-image re-hash that backfill_all_hashes does.
  3. stale    Flip statuses per (source, category): active listings not
              seen this run → stale; stale listings not seen for
              DELIST_DAYS → delisted. GUARDED three ways — see
              stage_stale. The important one: a category whose scrape
              stopped at its page cap is skipped entirely, because
              "not seen" only means "removed" if we looked at the whole
              list.
  4. dedup    Full persist pipeline (persist_dedup.main): clears and
              rebuilds matches + properties, links every listing.

Usage:
  python -m scripts.refresh --check                 # read-only preflight
  python -m scripts.refresh --scrape --max-pages 3  # smoke-test scrapers
  python -m scripts.refresh --all                   # the real thing
  python -m scripts.refresh --hashes --stale --dedup  # resume after scrape

NOTE: --dedup REBUILDS the properties table, so property IDs change every
run. Fine while nothing public links to them; must be fixed (stable
property identity) before SEO pages launch. Tracked in phase 1 plan.
"""
import argparse
import sys
from datetime import datetime, timedelta, timezone

from backend.db.client import get_client

STALE_STATUSES = ("active", "stale", "delisted")
DELIST_DAYS = 7           # stale this long (by last_seen) → delisted
MIN_SCRAPE_RATIO = 0.5    # scrape must save >= 50% of source's active count


# ── helpers ──────────────────────────────────────────────────────────

def count_listings(db, source_id=None, status=None, category=None) -> int:
    q = db.table("listings").select("id", count="exact")
    if source_id is not None:
        q = q.eq("source_id", source_id)
    if status is not None:
        q = q.eq("status", status)
    if category is not None:
        q = q.eq("category", category)
    return q.limit(1).execute().count or 0


def count_missing_hashes(db) -> int:
    return (db.table("listings")
            .select("id", count="exact")
            .not_.is_("image_url", "null")
            .is_("image_phash", "null")
            .limit(1)
            .execute()
            .count or 0)


# ── stage: check (read-only preflight) ───────────────────────────────

def stage_check(db):
    print("=== Preflight (read-only) ===")
    for sid, name in ((1, "BRK"), (2, "P24")):
        total = count_listings(db, source_id=sid)
        print(f"  {name}: {total} listings", end="")
        parts = []
        for st in STALE_STATUSES:
            n = count_listings(db, source_id=sid, status=st)
            parts.append(f"{st}={n}")
        # status=NULL rows predate status handling — need the SQL backfill
        null_status = total - sum(
            count_listings(db, source_id=sid, status=st)
            for st in STALE_STATUSES
        )
        print(f"  ({', '.join(parts)}, NULL={null_status})")

    print(f"  Listings missing image hashes: {count_missing_hashes(db)}")

    ph = db.table("price_history").select("id", count="exact").limit(1).execute()
    ph_count = ph.count or 0
    hint = " (0 rows — run backend/db/refresh_support.sql first)" if ph_count == 0 else ""
    print(f"  price_history rows: {ph_count}{hint}")

    props = db.table("properties").select("id", count="exact").limit(1).execute()
    print(f"  properties: {props.count or 0}")
    print("=== Preflight done — no writes performed ===")


# ── stage: scrape ────────────────────────────────────────────────────

def stage_scrape(db, max_pages=None, rent_max_pages=None, sources="both"):
    """Run both scrapers.

    Returns {source_id: {"saved": int, "pre_active": int, "categories":
    {category: {"saved": int, "complete": bool, "pre_active": int}}}}.

    rent_max_pages caps ONLY the rent category — free-tier disk can't
    hold the full P24 rental corpus (500MB tier), so we load the first N
    pages. The per-category "complete" flag is what stops that cap from
    corrupting stale detection: everything past the cap was never looked
    at, so its absence proves nothing. See stage_stale.
    """
    from scripts import scrape_buyrentkenya, scrape_property24

    # P24 blocks datacenter IPs (GitHub runners) with sustained 503s, so
    # scheduled CI runs use --sources brk; P24 scrapes run from
    # residential IPs (local/laptop) every few days. A source that isn't
    # attempted is skipped by the stale guard and the failure check.
    jobs = [(1, "BRK", scrape_buyrentkenya), (2, "P24", scrape_property24)]
    if sources == "brk":
        jobs = jobs[:1]
    elif sources == "p24":
        jobs = jobs[1:]

    results = {}
    for sid, name, mod in jobs:
        pre_active = count_listings(db, source_id=sid, status="active")
        print(f"\n=== Scraping {name} (currently {pre_active} active) ===")
        saved = 0
        per_category = {}
        for category in ("sale", "rent"):
            cat_pre = count_listings(db, source_id=sid, status="active",
                                     category=category)
            cap = max_pages
            if category == "rent" and rent_max_pages is not None:
                cap = min(rent_max_pages, max_pages) if max_pages else rent_max_pages
            try:
                n, complete = mod.run(max_pages=cap, category=category)
            except Exception as e:
                # A crash leaves the tail of the list unseen, so it is
                # never "complete" — staling on a crashed scrape is how
                # you lose a source.
                print(f"!! {name}/{category} scrape crashed: {e}")
                n, complete = 0, False
            per_category[category] = {"saved": n, "complete": complete,
                                      "pre_active": cat_pre}
            saved += n
        results[sid] = {"saved": saved, "pre_active": pre_active,
                        "categories": per_category}
    return results


# ── stage: hashes (incremental) ──────────────────────────────────────

def stage_hashes(db):
    """Hash images only for listings that don't have hashes yet."""
    from scripts.backfill_all_hashes import compute_hashes

    print("\n=== Incremental hash backfill ===")
    done = 0
    failed = 0
    while True:
        batch = (db.table("listings")
                 .select("id, image_url")
                 .not_.is_("image_url", "null")
                 .is_("image_phash", "null")
                 .order("id")
                 .range(0, 99)
                 .execute())
        if not batch.data:
            break

        # Hash each distinct URL once, update all rows sharing it
        by_url = {}
        for row in batch.data:
            by_url.setdefault(row["image_url"], []).append(row["id"])

        progress = False
        for url, ids in by_url.items():
            try:
                hashes = compute_hashes(url)
                db.table("listings").update(hashes).in_("id", ids).execute()
                done += len(ids)
                progress = True
            except Exception as e:
                # Mark unfetchable images so we don't loop on them forever:
                # empty-string phash = "tried, image gone" (excluded from
                # blocking since it can't match a real hash).
                db.table("listings") \
                    .update({"image_phash": ""}) \
                    .in_("id", ids) \
                    .execute()
                failed += len(ids)
                print(f"  Failed ({e}): {url[:80]}")

        if not progress and failed == 0:
            break  # safety: nothing happened this loop
        if done and done % 500 < len(batch.data):
            print(f"  {done} hashed so far...")

    print(f"  Hashed {done} listings, {failed} marked unfetchable.")
    return done


# ── stage: stale detection ───────────────────────────────────────────

def _flip_status_chunked(db, sid, category, from_status, new_status,
                         before_field, before_value, chunk=1000):
    """Flip listing status in id-batches so no single request exceeds
    Supabase's ~60s API-gateway upstream timeout. A full-table UPDATE
    504s on the free tier — and a windowed P24 rescrape can stale
    thousands of out-of-window rows at once. Returns the count flipped.

    Scoped to one category because sale and rent are crawled to different
    depths: rent is capped, sale usually is not, so they cannot share a
    stale decision.
    """
    from scripts.persist_dedup import _with_retry
    total = 0
    while True:
        q = _with_retry(lambda: db.table("listings").select("id")
                        .eq("source_id", sid).eq("category", category)
                        .eq("status", from_status)
                        .lt(before_field, before_value)
                        .order("id").limit(chunk).execute())
        ids = [r["id"] for r in q.data]
        if not ids:
            break
        _with_retry(lambda ids=ids: db.table("listings")
                    .update({"status": new_status}).in_("id", ids).execute())
        total += len(ids)
    return total


def stage_stale(db, run_start, scrape_results=None, force=False):
    """Flip active→stale (not seen this run) and stale→delisted (old).

    Runs per (source, category), because the two categories are crawled
    to different depths and a decision valid for one is invalid for the
    other. Three guards, in order:

    1. Source not scraped this run (CI does BRK only — P24 blocks
       datacenter IPs). No "seen this run" signal at all, so staling
       would condemn its entire active set.
    2. Category's scrape did not reach the end of pagination. Under a
       page cap, "not seen" means "not looked at", not "removed". This
       guard is the fix for 2026-08-12, when a capped rent scrape staled
       7,240 live P24 rentals — 41% of everything then marked gone.
    3. Category saved < MIN_SCRAPE_RATIO of its own prior active count —
       a broken parser, or a block that happened to look like the end of
       pagination.

    --force overrides 2 and 3, never 1: there is no cutoff that makes
    staling an unscraped source correct.
    """
    print("\n=== Stale detection ===")
    cutoff = run_start.isoformat()
    delist_cutoff = (run_start - timedelta(days=DELIST_DAYS)).isoformat()

    for sid, name in ((1, "BRK"), (2, "P24")):
        if scrape_results is not None and sid not in scrape_results:
            print(f"  {name}: not scraped this run — skipping stale pass.")
            continue

        # None in --stale resume mode, where the caller supplied an
        # explicit cutoff on purpose and takes responsibility for it.
        categories = (scrape_results or {}).get(sid, {}).get("categories", {})

        for category in ("sale", "rent"):
            info = categories.get(category)
            label = f"{name}/{category}"

            if info is not None and not force:
                if not info["complete"]:
                    print(f"  {label}: scrape stopped at the page cap, not "
                          f"the end of the list — SKIPPING stale pass "
                          f"(everything past the cap was never checked).")
                    continue
                saved, cat_pre = info["saved"], info["pre_active"]
                if cat_pre > 0 and saved < cat_pre * MIN_SCRAPE_RATIO:
                    print(f"  {label}: saved {saved} vs {cat_pre} active "
                          f"(< {MIN_SCRAPE_RATIO:.0%}) — SKIPPING stale pass. "
                          "Check the scraper, or rerun with --force.")
                    continue

            # stale → delisted first (order matters: don't delist rows
            # that only just went stale in this same pass). Both chunked
            # to stay under the 60s gateway limit.
            n_delisted = _flip_status_chunked(
                db, sid, category, "stale", "delisted",
                "last_seen", delist_cutoff)

            # active + not seen this run → stale
            n_stale = _flip_status_chunked(
                db, sid, category, "active", "stale", "last_seen", cutoff)

            print(f"  {label}: {n_stale} → stale, {n_delisted} → delisted")


# ── stage: dedup ─────────────────────────────────────────────────────

def stage_dedup():
    print("\n=== Dedup persist pipeline ===")
    from scripts.persist_dedup import main as persist_main
    persist_main()

    # DISABLED 2026-08-12. hide_generic_images() nulls the image on any
    # property whose phash is shared by >= 5 properties. That was aimed at
    # stock placeholders (auction boards, "no photo available") and caught
    # 523 properties while the corpus was P24 houses.
    #
    # After ingesting BRK apartments-for-sale it hit 3,733 — 11% of all
    # active properties left with no image. The groups are not
    # placeholders: e.g. 212 properties, every one 4-bed, all 13-20M,
    # across Kileleshwa/Kilimani/Lavington (three adjacent suburbs). A
    # real placeholder shows up on 1-beds at 3M and 6-beds at 300M alike;
    # these are coherent, so they are one development's render on
    # individually-listed units.
    #
    # So the function was hiding the evidence of a dedup miss rather than
    # fixing an image problem: 4,183 active listings sit in 96 such
    # groups and 1,417 of their pairs are unresolved in the review queue,
    # because price varies across a development and drags the score under
    # the auto-merge cut.
    #
    # A blank card is worse than an honest shared render, so this stays
    # off until the real question is settled: should a development's
    # individually-listed units collapse into ONE property with a price
    # range? The schema already supports that (price_min/price_max,
    # beds_min/beds_max). Tracked in docs/product-backlog.md.
    #
    # If it is ever re-enabled, the threshold is not the fix — the rule is
    # wrong in kind, not degree. Price/bed coherence separates a
    # placeholder from a development; property count does not.
    print("  (generic-image pass disabled — see comment / product-backlog)")


# ── main ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Re-scrape orchestrator (see module docstring)")
    parser.add_argument("--check", action="store_true",
                        help="read-only preflight, no writes")
    parser.add_argument("--scrape", action="store_true")
    parser.add_argument("--hashes", action="store_true")
    parser.add_argument("--stale", action="store_true")
    parser.add_argument("--dedup", action="store_true")
    parser.add_argument("--all", action="store_true",
                        help="scrape + hashes + stale + dedup")
    parser.add_argument("--max-pages", type=int, default=None,
                        help="limit pages per scraper (smoke tests)")
    parser.add_argument("--rent-max-pages", type=int, default=None,
                        help="cap ONLY the rent category (free-tier disk)")
    parser.add_argument("--sources", choices=("both", "brk", "p24"),
                        default="both",
                        help="which sources to scrape (CI uses brk — "
                             "P24 blocks datacenter IPs)")
    parser.add_argument("--stale-cutoff", type=str, default=None,
                        help="ISO timestamp to use as the stale cutoff "
                             "instead of now — REQUIRED when resuming a "
                             "crashed run's --stale stage (use the 'Run "
                             "started' line from that run's log), else "
                             "everything scraped before the resume gets "
                             "wrongly staled")
    parser.add_argument("--force", action="store_true",
                        help="run stale pass even if scrape looks broken")
    args = parser.parse_args()

    if not any((args.check, args.scrape, args.hashes, args.stale,
                args.dedup, args.all)):
        parser.print_help()
        sys.exit(1)

    db = get_client(timeout=600)
    run_start = datetime.now(timezone.utc)
    print(f"Run started {run_start.isoformat()}")

    if args.check:
        stage_check(db)
        return

    scrape_results = None
    if args.all or args.scrape:
        scrape_results = stage_scrape(db, max_pages=args.max_pages,
                                      rent_max_pages=args.rent_max_pages,
                                      sources=args.sources)

    if args.all or args.hashes:
        stage_hashes(db)

    if args.all or args.stale:
        cutoff = run_start
        if args.stale_cutoff:
            cutoff = datetime.fromisoformat(args.stale_cutoff)
            print(f"Using explicit stale cutoff: {cutoff.isoformat()}")
        # No blanket --max-pages skip any more. It only ever covered
        # --max-pages and silently ignored --rent-max-pages, which is
        # exactly how the 2026-08-12 mass-stale got through. stage_stale
        # now decides per category from the scraper's own "did I reach
        # the end" flag, which covers both caps and is strictly more
        # accurate than inspecting the CLI args.
        stage_stale(db, cutoff, scrape_results, force=args.force)

    if args.all or args.dedup:
        stage_dedup()

    print(f"\nRun finished ({datetime.now(timezone.utc).isoformat()}).")

    # Exit non-zero if any source/CATEGORY saved NOTHING — a dead parser
    # or blocked IP must alert (CI fails the job and emails), not shrug.
    # Per-category, because P24 once saved 21 sale + 0 rent and a
    # per-source total of 21 sailed through green. The <50% stale guard
    # already protected the DB either way.
    if scrape_results:
        names = {1: "BRK", 2: "P24"}
        dead = []
        for sid, res in scrape_results.items():
            for cat, info in res.get("categories", {}).items():
                if info["saved"] == 0:
                    dead.append(f"{names.get(sid, sid)}/{cat}")
        if dead:
            print(f"FAILURE: no listings saved for {', '.join(dead)} — "
                  "check parser/IP-blocking.")
            sys.exit(1)


if __name__ == "__main__":
    main()

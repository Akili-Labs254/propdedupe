"""Re-scrape orchestrator: scrape → hash new images → stale detection → dedup.

This is the script the scheduler will eventually run. Each stage is also
runnable on its own for debugging.

Stages (in --all order):
  1. scrape   Both scrapers. save_listings() upserts on (source_id,
              source_listing_id), bumps last_seen, resets status to active.
  2. hashes   Incremental hash backfill — only listings with an image_url
              but no image_phash (i.e. new since last run). NOT the full
              13k-image re-hash that backfill_all_hashes does.
  3. stale    Flip statuses per source: active listings not seen this run
              → stale; stale listings not seen for DELIST_DAYS → delisted.
              GUARDED: skipped for a source whose scrape looks like it
              failed (saved < MIN_SCRAPE_RATIO of its current active
              count), so a broken parser can't mass-stale a source.
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

def count_listings(db, source_id=None, status=None) -> int:
    q = db.table("listings").select("id", count="exact")
    if source_id is not None:
        q = q.eq("source_id", source_id)
    if status is not None:
        q = q.eq("status", status)
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
    """Run both scrapers. Returns {source_id: (saved, pre_run_active)}.

    rent_max_pages caps ONLY the rent category — free-tier disk can't
    hold the full P24 rental corpus (~7.5KB/listing, 500MB tier), so we
    load the first N pages. CAVEAT: on repeat capped runs, rent listings
    beyond the cap will churn stale/active as the site reorders pages —
    acceptable while rentals bootstrap, revisit at the tier upgrade.
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
            cap = max_pages
            if category == "rent" and rent_max_pages is not None:
                cap = min(rent_max_pages, max_pages) if max_pages else rent_max_pages
            try:
                n = mod.run(max_pages=cap, category=category)
            except Exception as e:
                print(f"!! {name}/{category} scrape crashed: {e}")
                n = 0
            per_category[category] = n
            saved += n
        results[sid] = (saved, pre_active, per_category)
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

def stage_stale(db, run_start, scrape_results=None, force=False):
    """Flip active→stale (not seen this run) and stale→delisted (old).

    scrape_results: {source_id: (saved, pre_run_active)} from stage_scrape.
    If a source's scrape saved < MIN_SCRAPE_RATIO of its previous active
    count, its stale pass is SKIPPED (unless --force) — a broken parser
    must not mass-stale a source.
    """
    print("\n=== Stale detection ===")
    cutoff = run_start.isoformat()
    delist_cutoff = (run_start - timedelta(days=DELIST_DAYS)).isoformat()

    for sid, name in ((1, "BRK"), (2, "P24")):
        # A scrape ran but this source wasn't attempted (e.g. CI does BRK
        # only — P24 blocks datacenter IPs). It has no "seen this run"
        # signal, so staling it would mark its ENTIRE active set stale.
        # Skip it. (scrape_results is None only in --stale resume mode,
        # where the caller passes an explicit cutoff on purpose.)
        if scrape_results is not None and sid not in scrape_results:
            print(f"  {name}: not scraped this run — skipping stale pass.")
            continue
        if scrape_results and not force:
            saved, pre_active = scrape_results.get(sid, (0, 0, {}))[:2]
            if pre_active > 0 and saved < pre_active * MIN_SCRAPE_RATIO:
                print(f"  {name}: scrape saved {saved} vs {pre_active} active "
                      f"(< {MIN_SCRAPE_RATIO:.0%}) — SKIPPING stale pass. "
                      "Check the scraper, or rerun with --force.")
                continue

        # stale → delisted first (order matters: don't delist rows that
        # only just went stale in this same pass)
        r1 = (db.table("listings")
              .update({"status": "delisted"})
              .eq("source_id", sid)
              .eq("status", "stale")
              .lt("last_seen", delist_cutoff)
              .execute())

        # active + not seen this run → stale
        r2 = (db.table("listings")
              .update({"status": "stale"})
              .eq("source_id", sid)
              .eq("status", "active")
              .lt("last_seen", cutoff)
              .execute())

        print(f"  {name}: {len(r2.data or [])} → stale, "
              f"{len(r1.data or [])} → delisted")


# ── stage: dedup ─────────────────────────────────────────────────────

def stage_dedup():
    print("\n=== Dedup persist pipeline ===")
    from scripts.persist_dedup import main as persist_main
    persist_main()

    # Hide stock placeholder images (auction signs etc.) — see
    # backend/db/admin_stats.sql. Non-fatal if the function is missing.
    try:
        db = get_client(timeout=600)
        res = db.rpc("hide_generic_images", {}).execute()
        print(f"  Generic images hidden on {res.data} properties")
    except Exception as e:
        print(f"  (generic-image pass skipped: {e})")


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
        if args.max_pages and not args.force:
            print("\n--max-pages set: skipping stale pass (partial scrape "
                  "would mass-stale everything). Use --force to override.")
        else:
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
        for sid, tup in scrape_results.items():
            per_category = tup[2] if len(tup) > 2 else {}
            for cat, n in per_category.items():
                if n == 0:
                    dead.append(f"{names.get(sid, sid)}/{cat}")
        if dead:
            print(f"FAILURE: no listings saved for {', '.join(dead)} — "
                  "check parser/IP-blocking.")
            sys.exit(1)


if __name__ == "__main__":
    main()

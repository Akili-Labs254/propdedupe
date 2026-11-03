"""Scrape Property24 Kenya listing pages into the listings table.

Pagination is dynamic: stops after 2 consecutive empty pages (page counts
drift as the site changes), with a hard cap as a runaway guard.

Run: python -m scripts.scrape_property24
"""
from datetime import datetime
from typing import Optional, Tuple

from backend.scraper.base import is_end_of_pagination
from backend.scraper.property24 import Property24Scraper
from backend.db.save import save_listings

# Category → the index URLs that make it up. Category is a hard partition
# in the DB (see save.py); P24 splits inventory by property type, so one
# category can span several indexes.
#
# DELIBERATELY NOT INCLUDED: apartments-flats-for-sale-in-nairobi-p95.
# Measured 2026-08-12 (scripts/probe_depth.py):
#
#   houses-for-sale           10,708 listings    536 pages    45 MB
#   apartments-flats-for-sale 88,820 listings  4,441 pages   373 MB
#   apartments-flats-to-rent  84,500 listings  4,225 pages   355 MB
#
# Free-tier headroom is ~205 MB, so apartments cannot be crawled to the
# end. That matters beyond disk: `complete` is an AND across a category's
# indexes, so adding a permanently-capped index would stop the whole sale
# category from staling, and sale is the ONE P24 category that currently
# completes and stales correctly. Adding it would trade working stale
# detection for a partial slice of inventory.
#
# When disk allows, add it with ?SortOrder=PriceAscending so a capped
# crawl takes the cheapest listings, the sub-20M band we actually want,
# rather than an arbitrary slice of P24's default ordering.
CATEGORY_URLS = {
    "sale": [
        "https://www.property24.co.ke/houses-for-sale-in-nairobi-p95",
    ],
    "rent": [
        "https://www.property24.co.ke/apartments-flats-to-rent-in-nairobi-p95",
    ],
}
SOURCE_ID = 2  # Property24 Kenya: second row inserted into sources
HARD_PAGE_CAP = 6000         # runaway guard (P24 rent is thousands of pages)
MAX_CONSECUTIVE_EMPTY = 2    # stop after this many empty pages in a row


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def _crawl_index(base_url: str, cap: int, category: str) -> Tuple[int, bool]:
    """Crawl one index URL to `cap` pages. Returns (saved, complete)."""
    scraper = Property24Scraper()
    saved = 0
    empty_streak = 0
    fail_streak = 0
    streak_is_end = True   # every failure so far was a 4xx
    complete = False

    for page in range(1, cap + 1):
        url = base_url if page == 1 else f"{base_url}?Page={page}"
        log(f"[P24/{category}] page {page}...")

        try:
            html = scraper.fetch(url)
            listings = scraper.parse(html)
            fail_streak = 0
            streak_is_end = True
        except Exception as e:
            fail_streak += 1
            # A streak only means "end of list" if every failure in it
            # was a 4xx. One 5xx anywhere in the streak and we have no
            # idea where the list ends. See base.is_end_of_pagination.
            streak_is_end = streak_is_end and is_end_of_pagination(e)
            print(f"  Failed: {e} (fail streak {fail_streak})")
            if fail_streak >= 3:
                if streak_is_end:
                    log(f"[P24/{category}] {fail_streak} 404s in a row — "
                        "end of pagination.")
                    complete = True
                else:
                    log(f"[P24/{category}] {fail_streak} server errors in a "
                        "row — NOT the end of the list. Marking incomplete "
                        "so stale detection skips this category.")
                    complete = False
                break
            continue

        if not listings:
            empty_streak += 1
            print(f"  0 listings (empty streak {empty_streak})")
            if empty_streak >= MAX_CONSECUTIVE_EMPTY:
                log(f"[P24] {empty_streak} empty pages in a row — done.")
                complete = True
                break
            continue

        empty_streak = 0
        save_listings(listings, SOURCE_ID, category=category)
        saved += len(listings)
        print(f"  Saved {len(listings)} listings ({saved} total)")

    how = ("reached end of pagination" if complete
           else f"INCOMPLETE — hit cap ({cap}) or server errors")
    log(f"[P24/{category}] {base_url.rsplit('/', 1)[-1]}: "
        f"{saved} saved — {how}.")
    return saved, complete


def run(max_pages: Optional[int] = None,
        category: str = "sale") -> Tuple[int, bool]:
    """Scrape every index in `category` and return (listings_saved, complete).

    complete is True only when EVERY index in the category ended
    pagination on its own: an empty-page or 404 streak means we reached
    the real end of that list. It is False when any index ran out of
    `cap`, because then part of the category was never looked at.

    refresh.stage_stale depends on this. "Not seen this run" is only
    evidence a listing was removed if we actually looked at the whole
    list; under a cap it just means we stopped early. Reporting
    complete=True after a capped run staled 7,240 live P24 rentals on
    2026-08-12: 41% of everything then marked gone. See ADR 0008.

    `cap` applies PER INDEX, not per category: two sale indexes with
    max_pages=1500 crawl up to 3,000 pages. Predictable per URL, but
    watch it when disk is the binding constraint.
    """
    cap = min(max_pages, HARD_PAGE_CAP) if max_pages else HARD_PAGE_CAP
    total = 0
    complete = True
    for base_url in CATEGORY_URLS[category]:
        saved, done = _crawl_index(base_url, cap, category)
        total += saved
        complete = complete and done
    log(f"[P24/{category}] finished: {total} listings saved across "
        f"{len(CATEGORY_URLS[category])} index(es), complete={complete}.")
    return total, complete


if __name__ == "__main__":
    import sys
    run(category="rent" if "--rent" in sys.argv else "sale")

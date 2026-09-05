"""Scrape BuyRentKenya listing pages into the listings table.

Pagination is dynamic: stops after 2 consecutive empty pages (page counts
drift as the site changes), with a hard cap as a runaway guard.

Run: python -m scripts.scrape_buyrentkenya
"""
from typing import Optional, Tuple

from backend.scraper.base import is_end_of_pagination
from backend.scraper.buyrentkenya import BuyRentKenyaScraper
from backend.db.save import save_listings

# Category → the index URLs that make it up. Category is a hard
# partition in the DB (see save.py); BRK splits by property type, so one
# category spans several indexes.
#
# flats-apartments-for-sale was missing until 2026-08-12: no apartments
# in the sale corpus at all, so the sub-20M band was barely represented.
#
# Unlike P24, BRK fits. Measured 2026-08-12 (scripts/probe_depth.py):
#
#   houses-for-sale            1,353 listings   68 pages    6 MB
#   flats-apartments-for-sale  6,060 listings  303 pages   25 MB
#   flats-apartments-for-rent  4,218 listings  211 pages   18 MB
#
# 49 MB for the whole source against ~205 MB of headroom, so every BRK
# category can be crawled to the end — which is the only way its listings
# ever get staled (ADR 0008). P24 cannot say the same at 773 MB.
#
# Rent stays apartments-only (the high-churn, high-duplication wedge);
# houses-to-rent can join once disk allows.
CATEGORY_URLS = {
    "sale": [
        "https://www.buyrentkenya.com/houses-for-sale/nairobi",
        "https://www.buyrentkenya.com/flats-apartments-for-sale/nairobi",
    ],
    "rent": [
        "https://www.buyrentkenya.com/flats-apartments-for-rent/nairobi",
    ],
}
SOURCE_ID = 1
HARD_PAGE_CAP = 600          # runaway guard (rent was ~195 pages July 2026)
MAX_CONSECUTIVE_EMPTY = 2    # stop after this many empty pages in a row


def _crawl_index(base_url: str, cap: int, category: str) -> Tuple[int, bool]:
    """Crawl one index URL to `cap` pages. Returns (saved, complete)."""
    scraper = BuyRentKenyaScraper()
    saved = 0
    empty_streak = 0
    fail_streak = 0
    streak_is_end = True   # every failure so far was a 4xx
    complete = False

    for page in range(1, cap + 1):
        url = base_url if page == 1 else f"{base_url}?page={page}"
        print(f"[BRK/{category}] page {page}...")

        try:
            html = scraper.fetch(url)
            listings = scraper.parse(html)
            fail_streak = 0
            streak_is_end = True
        except Exception as e:
            fail_streak += 1
            # Only a streak of pure "not found" means end-of-list. A 403 is
            # a refusal, not an absence — see is_end_of_pagination.
            streak_is_end = streak_is_end and is_end_of_pagination(e)
            print(f"  Failed: {e} (fail streak {fail_streak})")
            if fail_streak >= 3:
                if streak_is_end:
                    print(f"[BRK/{category}] {fail_streak} not-founds in a "
                          "row — end of pagination.")
                    complete = True
                else:
                    print(f"[BRK/{category}] {fail_streak} server errors in a "
                          "row — NOT the end of the list. Marking incomplete "
                          "so stale detection skips this category.")
                    complete = False
                break
            continue

        if not listings:
            empty_streak += 1
            print(f"  0 listings (empty streak {empty_streak})")
            if empty_streak >= MAX_CONSECUTIVE_EMPTY:
                print(f"[BRK] {empty_streak} empty pages in a row — done.")
                complete = True
                break
            continue

        empty_streak = 0
        save_listings(listings, SOURCE_ID, category=category)
        saved += len(listings)
        print(f"  Saved {len(listings)} listings ({saved} total)")

    how = ("reached end of pagination" if complete
           else f"INCOMPLETE — hit cap ({cap}) or server errors")
    print(f"[BRK/{category}] {base_url.rsplit('/', 2)[-2]}: "
          f"{saved} saved — {how}.")
    return saved, complete


def run(max_pages: Optional[int] = None,
        category: str = "sale") -> Tuple[int, bool]:
    """Scrape every index in `category` and return (listings_saved, complete).

    complete is True only when EVERY index in the category ended
    pagination on its own — for BRK that is the 404 streak, since it does
    not serve empty pages past the end. It is False when any index ran
    out of `cap`: refresh.stage_stale must not read "not seen this run"
    as "removed from the site" for a list it never finished. See ADR 0008.

    `cap` applies PER INDEX, not per category.
    """
    cap = min(max_pages, HARD_PAGE_CAP) if max_pages else HARD_PAGE_CAP
    total = 0
    complete = True
    for base_url in CATEGORY_URLS[category]:
        saved, done = _crawl_index(base_url, cap, category)
        total += saved
        complete = complete and done
    print(f"[BRK/{category}] finished: {total} listings saved across "
          f"{len(CATEGORY_URLS[category])} index(es), complete={complete}.")
    return total, complete


if __name__ == "__main__":
    import sys
    run(category="rent" if "--rent" in sys.argv else "sale")

"""Scrape Property24 Kenya listing pages into the listings table.

Pagination is dynamic: stops after 2 consecutive empty pages (page counts
drift as the site changes), with a hard cap as a runaway guard.

Run: python -m scripts.scrape_property24
"""
from datetime import datetime
from typing import Optional, Tuple

from backend.scraper.property24 import Property24Scraper
from backend.db.save import save_listings

# Category → listing index URL. Rent wedge = apartments/flats.
CATEGORY_URLS = {
    "sale": "https://www.property24.co.ke/houses-for-sale-in-nairobi-p95",
    "rent": "https://www.property24.co.ke/apartments-flats-to-rent-in-nairobi-p95",
}
SOURCE_ID = 2  # Property24 Kenya — second row inserted into sources
HARD_PAGE_CAP = 6000         # runaway guard (P24 rent is thousands of pages)
MAX_CONSECUTIVE_EMPTY = 2    # stop after this many empty pages in a row


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def run(max_pages: Optional[int] = None,
        category: str = "sale") -> Tuple[int, bool]:
    """Scrape pages and return (listings_saved, complete).

    complete is True only when pagination ended on its own — an empty-page
    or 404 streak means we reached the real end of the list. It is False
    when the loop simply ran out of `cap`, because then everything past
    the cap was never looked at.

    refresh.stage_stale depends on this distinction. "Not seen this run"
    is only evidence a listing was removed if we actually looked at the
    whole list; under a cap it just means we stopped early. Reporting
    complete=True after a capped run staled 7,240 live P24 rentals on
    2026-08-12 — 41% of everything marked gone.
    """
    base_url = CATEGORY_URLS[category]
    scraper = Property24Scraper()
    saved = 0
    empty_streak = 0
    fail_streak = 0
    complete = False
    cap = min(max_pages, HARD_PAGE_CAP) if max_pages else HARD_PAGE_CAP

    for page in range(1, cap + 1):
        url = base_url if page == 1 else f"{base_url}?Page={page}"
        log(f"[P24/{category}] page {page}...")

        try:
            html = scraper.fetch(url)
            listings = scraper.parse(html)
            fail_streak = 0
        except Exception as e:
            # Consecutive failures = end of pagination (sites 404 or
            # error past the last page rather than serving empty pages).
            fail_streak += 1
            print(f"  Failed: {e} (fail streak {fail_streak})")
            if fail_streak >= 3:
                # Sites 404 past the last page rather than serving an
                # empty one, so this is a normal terminal condition, not
                # an anomaly — hence complete=True. A mid-run IP block
                # would also land here and wrongly look complete; the
                # MIN_SCRAPE_RATIO guard in refresh.stage_stale is the
                # second line of defence against that.
                log(f"[P24/{category}] {fail_streak} failures in a row — done.")
                complete = True
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

    how = "reached end of pagination" if complete else f"stopped at cap ({cap})"
    log(f"[P24/{category}] finished: {saved} listings saved — {how}.")
    return saved, complete


if __name__ == "__main__":
    import sys
    run(category="rent" if "--rent" in sys.argv else "sale")

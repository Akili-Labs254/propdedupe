"""Scrape Property24 Kenya listing pages into the listings table.

Pagination is dynamic: stops after 2 consecutive empty pages (page counts
drift as the site changes), with a hard cap as a runaway guard.

Run: python -m scripts.scrape_property24
"""
from datetime import datetime
from typing import Optional

from backend.scraper.property24 import Property24Scraper
from backend.db.save import save_listings

BASE_URL = "https://www.property24.co.ke/houses-for-sale-in-nairobi-p95"
SOURCE_ID = 2  # Property24 Kenya — second row inserted into sources
HARD_PAGE_CAP = 1200         # runaway guard; last known real count was ~579
MAX_CONSECUTIVE_EMPTY = 2    # stop after this many empty pages in a row


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def run(max_pages: Optional[int] = None) -> int:
    """Scrape all pages (or up to max_pages) and return listings saved."""
    scraper = Property24Scraper()
    saved = 0
    empty_streak = 0
    cap = min(max_pages, HARD_PAGE_CAP) if max_pages else HARD_PAGE_CAP

    for page in range(1, cap + 1):
        url = BASE_URL if page == 1 else f"{BASE_URL}?Page={page}"
        log(f"[P24] page {page}...")

        try:
            html = scraper.fetch(url)
            listings = scraper.parse(html)
        except Exception as e:
            print(f"  Failed: {e}, skipping...")
            continue

        if not listings:
            empty_streak += 1
            print(f"  0 listings (empty streak {empty_streak})")
            if empty_streak >= MAX_CONSECUTIVE_EMPTY:
                log(f"[P24] {empty_streak} empty pages in a row — done.")
                break
            continue

        empty_streak = 0
        save_listings(listings, SOURCE_ID)
        saved += len(listings)
        print(f"  Saved {len(listings)} listings ({saved} total)")

    log(f"[P24] finished: {saved} listings saved.")
    return saved


if __name__ == "__main__":
    run()

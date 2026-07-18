"""Scrape BuyRentKenya listing pages into the listings table.

Pagination is dynamic: stops after 2 consecutive empty pages (page counts
drift as the site changes), with a hard cap as a runaway guard.

Run: python -m scripts.scrape_buyrentkenya
"""
from typing import Optional

from backend.scraper.buyrentkenya import BuyRentKenyaScraper
from backend.db.save import save_listings

# Category → listing index URL. Rent wedge = apartments/flats (the
# high-churn, high-duplication segment); houses-to-rent can join later.
CATEGORY_URLS = {
    "sale": "https://www.buyrentkenya.com/houses-for-sale/nairobi",
    "rent": "https://www.buyrentkenya.com/flats-apartments-for-rent/nairobi",
}
SOURCE_ID = 1
HARD_PAGE_CAP = 600          # runaway guard (rent was ~195 pages July 2026)
MAX_CONSECUTIVE_EMPTY = 2    # stop after this many empty pages in a row


def run(max_pages: Optional[int] = None, category: str = "sale") -> int:
    """Scrape all pages (or up to max_pages) and return listings saved."""
    base_url = CATEGORY_URLS[category]
    scraper = BuyRentKenyaScraper()
    saved = 0
    empty_streak = 0
    fail_streak = 0
    cap = min(max_pages, HARD_PAGE_CAP) if max_pages else HARD_PAGE_CAP

    for page in range(1, cap + 1):
        url = base_url if page == 1 else f"{base_url}?page={page}"
        print(f"[BRK/{category}] page {page}...")

        try:
            html = scraper.fetch(url)
            listings = scraper.parse(html)
            fail_streak = 0
        except Exception as e:
            # BRK 404s past the last page (it does NOT serve an empty
            # page), so consecutive failures = end of pagination.
            fail_streak += 1
            print(f"  Failed: {e} (fail streak {fail_streak})")
            if fail_streak >= 3:
                print(f"[BRK/{category}] {fail_streak} failures in a row — done.")
                break
            continue

        if not listings:
            empty_streak += 1
            print(f"  0 listings (empty streak {empty_streak})")
            if empty_streak >= MAX_CONSECUTIVE_EMPTY:
                print(f"[BRK] {empty_streak} empty pages in a row — done.")
                break
            continue

        empty_streak = 0
        save_listings(listings, SOURCE_ID, category=category)
        saved += len(listings)
        print(f"  Saved {len(listings)} listings ({saved} total)")

    print(f"[BRK/{category}] finished: {saved} listings saved.")
    return saved


if __name__ == "__main__":
    import sys
    run(category="rent" if "--rent" in sys.argv else "sale")

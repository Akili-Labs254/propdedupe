from backend.scraper.property24 import Property24Scraper
from backend.db.save import save_listings

BASE_URL = "https://www.property24.co.ke/houses-for-sale-in-nairobi-p95"
SOURCE_ID = 2  # Property24 Kenya — second row inserted into sources
TOTAL_PAGES = 3

def main():
    scraper = Property24Scraper()

    for page in range(1, TOTAL_PAGES + 1):
        url = BASE_URL if page == 1 else f"{BASE_URL}?Page={page}"
        print(f"Scraping page {page}/{TOTAL_PAGES}...")

        try:
            html = scraper.fetch(url)
            listings = scraper.parse(html)
            save_listings(listings, SOURCE_ID)
            print(f"  Saved {len(listings)} listings")
        except Exception as e:
            print(f"  Failed: {e}, skipping...")
            continue

if __name__ == "__main__":
    main()
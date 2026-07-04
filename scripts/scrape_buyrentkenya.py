import time
from backend.scraper.buyrentkenya import BuyRentKenyaScraper
from backend.db.save import save_listings

BASE_URL = "https://www.buyrentkenya.com/houses-for-sale/nairobi"
SOURCE_ID = 1
TOTAL_PAGES = 59

def main():
    scraper = BuyRentKenyaScraper()
    
    for page in range(1, TOTAL_PAGES + 1):
        url = BASE_URL if page == 1 else f"{BASE_URL}?page={page}"
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
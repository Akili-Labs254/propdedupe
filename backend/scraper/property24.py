"""Property24 Kenya scraper (HTML-selector strategy).

STATUS: stub — implement in Week 1. The selectors below are the spec from
scraping recon; fill in parse() to turn them into ScrapedListing objects.

Source notes (property24.co.ke):
  - Server-side rendered, jQuery-based. requests + BeautifulSoup is enough.
  - Listing container : div.js_listingTile        (has data-listing-number)
  - Price             : span.p24_price            -> content="72000000"
  - Title             : meta[itemprop="name"]     -> "5 Bedroom House"
  - Location          : span.p24_location
  - Address           : span.p24_address
  - Beds/baths/parking: span.p24_featureDetails   (ordered: beds, baths, parking)
  - Floor size        : span.p24_size
  - Image             : <image> src inside span.js_listingTileImageHolder
  - Listing URL       : <a> href inside the tile
  - Agency            : meta[itemprop="name"] inside span[itemtype=".../RealEstateAgent"]
  - Pagination        : ?Page=N  (e.g. /houses-for-sale-in-nairobi-p95?Page=2)

Dedup signal: exact-duplicate listings share an IDENTICAL image URL here.
"""
from backend.scraper.base import BaseScraper, ScrapedListing
from bs4 import BeautifulSoup
import random
import time

class Property24Scraper(BaseScraper):
    source_name = "Property24 Kenya"
    base_url = "https://www.property24.co.ke"

    def parse(self, html: str) -> list:
        soup = BeautifulSoup(html, "html.parser")
        tiles = soup.select("div.js_listingTile")

        listings = []
        for tile in tiles:
            listing = self._parse_tile(tile)
            if listing:
                listings.append(listing)
        return listings
    
    
    def _parse_tile(self, tile):
        listing_id = tile.get("data-listing-number")

        price_tag = tile.select_one("span.p24_price")
        price = None
        if price_tag and price_tag.get("content"):
            price = float(price_tag["content"])

        title_tag = tile.select_one('meta[itemprop="name"]')
        title = title_tag.get("content") if title_tag else None

        location_tag = tile.select_one("span.p24_location")
        location = location_tag.get_text(strip=True) if location_tag else None

        address_tag = tile.select_one("span.p24_address")
        address = address_tag.get_text(strip=True) if address_tag else None
        
        url_tag = tile.select_one("a")
        url = self.base_url + url_tag["href"] if url_tag and url_tag.get("href") else None

        image_tag = tile.select_one("span.js_listingTileImageHolder image")
        image_urls = [image_tag["src"]] if image_tag and image_tag.get("src") else []

        bedrooms = baths = None
        for feature in tile.select("span.p24_featureDetails"):
            label = feature.get("title", "")
            value_tag = feature.select_one("span")
            if not value_tag:
                continue
            value = value_tag.get_text(strip=True)
            if label == "Bedrooms" and value.replace(".", "").isdigit():
                parsed = float(value)
                if parsed == int(parsed) and parsed <= 20:
                    bedrooms = int(parsed)
            elif label == "Bathrooms" and value.replace(".", "").isdigit():
                parsed = float(value)
                if parsed <= 20:  # sanity ceiling — real listings never come close to this
                    baths = parsed

        size_tag = tile.select_one("span.p24_size span")
        size_sqm = None
        if size_tag:
            size_text = size_tag.get_text(strip=True)  # e.g. "600 m²"
            digits = size_text.split(" ")[0].replace(",", "")
            if digits.isdigit():
                size_sqm = float(digits)

        return ScrapedListing(
            source_listing_id=listing_id,
            title=title,
            price=price,
            location=location,
            url=url,
            image_urls=image_urls,
            bedrooms=bedrooms,
            baths=baths,
            raw=str(tile),
            size_sqm=size_sqm,
            address=address
        )
    
    def fetch_all(self, base_url: str, max_pages: int = 579):
        all_listings = []
        for page_num in range(1, max_pages + 1):
            url = base_url if page_num == 1 else f"{base_url}?Page={page_num}"
            html = self.fetch(url)
            page_listings = self.parse(html)

            if not page_listings:
                break

            all_listings.extend(page_listings)
            time.sleep(random.uniform(1, 2))

        return all_listings

"""Property24 Kenya scraper (HTML-selector strategy).

Source notes (property24.co.ke):
  - Server-side rendered, jQuery-based. requests + BeautifulSoup is enough.
  - Listing container : div.js_listingTile        (has data-listing-number)
  - Price             : span.p24_price            -> TEXT "KSh 145 000 000"
                        (was content="72000000" until ~26 Aug 2026: the
                        attribute was dropped and the change was silent)
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
from typing import Optional
import random
import re
import time

# The first number in a price string, allowing the separators P24 uses:
# ordinary spaces, NON-BREAKING spaces (: P24 has caught us with these
# before), commas and thin spaces. Stops at anything else, so a range like
# "KSh 1 200 000 - 2 000 000" yields the lower bound rather than welding the
# two numbers into one absurd figure.
PRICE_NUMBER_RE = re.compile(r"\d[\d\s  ,]*")


def parse_price_text(text: Optional[str]) -> Optional[float]:
    """Pull a price out of "KSh 145 000 000".

    Returns None for POA and anything without digits, never 0. A zero
    price sorts to the top of every price-ascending page, which is exactly
    where paid traffic lands.
    """
    if not text:
        return None
    match = PRICE_NUMBER_RE.search(text)
    if not match:
        return None
    digits = re.sub(r"\D", "", match.group(0))
    if not digits:
        return None
    value = float(digits)
    return value if value > 0 else None

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

        # 2026-08: P24 dropped the content attribute and moved the price
        # into the element's text ("KSh 145 000 000"). The old code read
        # only the attribute, so every scrape from ~26 Aug wrote NULL:
        # 10,083 sale listings and 30,000 rentals lost their price, and
        # nothing errored. price_history dutifully logged 37,000 prices
        # becoming NULL, which is how it was eventually found.
        # Attribute first (in case it returns), text as the real path.
        price_tag = tile.select_one("span.p24_price")
        price = None
        if price_tag:
            if price_tag.get("content"):
                price = parse_price_text(price_tag["content"])
            if price is None:
                price = parse_price_text(price_tag.get_text(" ", strip=True))

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
                if parsed <= 20:  # sanity ceiling: real listings never come close to this
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

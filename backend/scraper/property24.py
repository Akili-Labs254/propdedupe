"""Property24 Kenya scraper (HTML-selector strategy).

Source notes (property24.co.ke):
  - Server-side rendered, jQuery-based. requests + BeautifulSoup is enough.
  - Listing container : div.js_listingTile        (has data-listing-number)
  - Price             : span.p24_price            -> TEXT "KSh 145 000 000"
                        (was content="72000000" until ~26 Aug 2026: the
                        attribute was dropped and the change was silent)
  - Title             : <a title="..."> on the tile link
                        -> "2 Bedroom Apartment / Flat to Rent in Kilimani".
                        NOT the first meta[itemprop="name"]: rent tiles carry
                        only the agency's schema.org block, so that meta is
                        the agency's name (see _title_of)
  - Location          : span.p24_location
  - Address           : span.p24_address
  - Beds/baths/parking: span.p24_featureDetails   (ordered: beds, baths, parking)
  - Floor size        : span.p24_size
  - Image             : <img> src inside span.js_listingTileImageHolder
                        (was an SVG-style <image> until ~Nov 2026: changed
                        silently, like the price did in August)
  - Listing URL       : <a> href inside the tile
  - Agency            : meta[itemprop="name"] inside span[itemtype=".../RealEstateAgent"]
  - Pagination        : ?Page=N  (e.g. /houses-for-sale-in-nairobi-p95?Page=2)

Dedup signal: exact-duplicate listings share an IDENTICAL image URL here.
"""
from backend.scraper.base import BaseScraper, ScrapedListing, ParserOutOfDate
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

FOREIGN_CURRENCY_RE = re.compile(r"US\$|USD|\$|€|£|EUR|GBP", re.I)

# Floor area: a number with P24's thousands separators (spaces, NBSP, thin
# spaces, commas) and an optional decimal, followed by m². Anything in
# acres or hectares is a plot, not a floor area, and stays None.
SIZE_RE = re.compile("(\\d[\\d\\s\u00a0\u2009,]*(?:\\.\\d+)?)\\s*(m²|m2|sqm)", re.I)


def parse_size_text(text: Optional[str]) -> Optional[float]:
    """"1 200 m²" -> 1200.0. The old split-on-space read it as 1, and
    "102 120 m²" as 102 (a plausible-looking flat). Acres/ha -> None."""
    if not text:
        return None
    m = SIZE_RE.search(text)
    if not m:
        return None
    number = re.sub("[\\s\u00a0\u2009,]", "", m.group(1))
    try:
        value = float(number)
    except ValueError:
        return None
    return value if value > 0 else None


def _title_of(tile) -> Optional[str]:
    """The listing's own title, never the agency's.

    1. The tile link's title attribute, present on rent and sale tiles.
    2. A meta[itemprop=name] that is NOT inside the agency's
       schema.org/RealEstateAgent block (sale tiles have a Product one).
    3. The visible span.p24_propertyTitle ("2 Bedroom Apartment / Flat").

    Until 24 Nov 2026 this took the first meta[itemprop=name] in the tile.
    Rent tiles have no Product block, so that was the agency: of 46,968
    active rentals, ~11,300 were titled with an agency name and ~35,400
    had no title at all, and rent property pages rendered "None" as their
    heading and <title>.
    """
    for a in tile.select("a[title]"):
        title = (a.get("title") or "").strip()
        if title and "/" in (a.get("href") or ""):
            return title
    for meta in tile.select('meta[itemprop="name"]'):
        if meta.find_parent(attrs={"itemtype": re.compile("RealEstateAgent")}):
            continue
        content = (meta.get("content") or "").strip()
        if content:
            return content
    span = tile.select_one("span.p24_propertyTitle")
    if span:
        text = span.get_text(" ", strip=True)
        return text or None
    return None


def parse_price_text(text: Optional[str]) -> Optional[float]:
    """Pull a price out of "KSh 145 000 000".

    Returns None for POA and anything without digits, never 0. A zero
    price sorts to the top of every price-ascending page, which is exactly
    where paid traffic lands.
    """
    if not text:
        return None
    # Every stored price is KES (save.py writes price_currency="KES"). A
    # dollar figure read as shillings is off by ~130x, so refuse it rather
    # than store a number in the wrong currency. None seen as of Nov 2026.
    if FOREIGN_CURRENCY_RE.search(text):
        return None
    match = PRICE_NUMBER_RE.search(text)
    if not match:
        return None
    digits = re.sub(r"\D", "", match.group(0))
    if not digits:
        return None
    value = float(digits)
    return value if value > 0 else None

# A page needs at least this many listings before an all-missing field
# counts as a broken parser rather than a quirk of that page.
COVERAGE_MIN_LISTINGS = 5


def check_field_coverage(listings) -> None:
    """Refuse a page where a field that is always present came back empty.

    P24 has changed its markup silently twice: the price moved out of an
    attribute in August, and the cover image tag changed from <image> to
    <img> in November. Both times every listing still parsed, so nothing
    failed. The upsert then wrote NULL over the stored value for every
    listing the crawl touched (37,000 prices; 26,008 image URLs and their
    hashes). Raising here stops the page before anything is saved.
    """
    if len(listings) < COVERAGE_MIN_LISTINGS:
        return
    if not any(l.image_urls for l in listings):
        raise ParserOutOfDate(
            f"P24: {len(listings)} listings on the page and none has a cover "
            "image. The image markup has probably changed.")
    if not any(l.price is not None for l in listings):
        raise ParserOutOfDate(
            f"P24: {len(listings)} listings on the page and none has a price. "
            "The price markup has probably changed.")


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
        check_field_coverage(listings)
        return listings
    
    
    def _parse_tile(self, tile):
        listing_id = tile.get("data-listing-number")
        if not listing_id:
            # No id, nothing to key the row on (and it would fail the
            # page's whole upsert batch). Promo/ad tiles look like this.
            return None

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

        title = _title_of(tile)

        location_tag = tile.select_one("span.p24_location")
        location = location_tag.get_text(strip=True) if location_tag else None

        address_tag = tile.select_one("span.p24_address")
        address = address_tag.get_text(strip=True) if address_tag else None
        
        url_tag = tile.select_one("a")
        url = self.base_url + url_tag["href"] if url_tag and url_tag.get("href") else None

        # <img> since ~Nov 2026, <image> before. Accept both: when a field
        # selector stops matching, the result is a NULL written over good
        # data, not an error (see check_field_coverage).
        image_tag = tile.select_one("span.js_listingTileImageHolder img, "
                                    "span.js_listingTileImageHolder image")
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
        size_sqm = parse_size_text(
            size_tag.get_text(" ", strip=True)) if size_tag else None

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

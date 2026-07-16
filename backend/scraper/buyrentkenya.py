"""BuyRentKenya scraper (JSON-LD strategy).

STATUS: stub — implement in Week 1/2. No HTML parsing needed for core data;
the listings live in a <script type="application/ld+json"> block.

Source notes (buyrentkenya.com):
  - Laravel Livewire, server-side rendered.
  - Parse JSON-LD: @graph -> WebPage object -> mainEntity.itemListElement
  - Each item is a ListItem -> Product with:
      name      : "4 Bed Townhouse with En Suite in Runda"  (beds + area in title)
      image.url : full image URL
      category  : "Villas" | "Townhouses" | "Other Houses"
      description, offers.priceSpecification.price / .priceCurrency ("KES")
      @id       : contains listing id, pattern item-XXXXXXX (e.g. item-4014286)
  - Pagination : ?page=N  (22 per page)

Note: bedrooms & location are embedded in `name`, not separate fields — parse
them out with a small regex ("4 Bed" -> 4, "in Runda" -> Runda).
"""
from backend.scraper.base import BaseScraper, ScrapedListing
from typing import Optional, Tuple
import json
import re
from bs4 import BeautifulSoup

def parse_title(title : str) -> Tuple[Optional[int], Optional[str]]:
    bedrooms = None
    location = None

    bed_match = re.search(r"(\d+)\s+[Bd]ed", title)
    if bed_match:
        bedrooms = int(bed_match.group(1))

    loc_match = re.search(r"(?:in|at)\s+(.+)$", title)
    if loc_match:
        location = loc_match.group(1)

    return bedrooms, location


class BuyRentKenyaScraper(BaseScraper):
    source_name = "BuyRentKenya"
    base_url = "https://www.buyrentkenya.com"

    def parse(self, html: str) -> list[ScrapedListing]:
        # Function works, pulling from website,converting from json to py dict and parsing the data. 
        # Small edge case includes matching the location due to incosistent wording in listing ie "in" and "at" 
        
        soup = BeautifulSoup(html, "html.parser")
        block = soup.find("script", type = "application/ld+json")
        data = json.loads(block.string)

        graph = data.get("@graph", [])
        webpage = next((node for node in graph if node.get("@type") == "WebPage"), None)
        if not webpage:
            return []
        items = webpage.get("mainEntity", {}).get("itemListElement", [])

        # Rental JSON-LD omits the Product url (sale markup has it), so
        # fall back to the page's cards: each /listings/ anchor sits
        # right before an <h2> holding the exact listing name. Pair
        # href → name and look up per item; FIFO queues keep repeated
        # names (two identical adverts on one page) in page order.
        title_to_hrefs = {}
        for a in soup.select('a[href*="/listings/"]'):
            href = (a.get("href") or "").split("?")[0]
            if href.startswith("/"):
                href = self.base_url + href
            h2 = a.find_next("h2")
            if href and h2:
                key = h2.get_text(strip=True).casefold()
                queue = title_to_hrefs.setdefault(key, [])
                if href not in queue:
                    queue.append(href)

        listings = []
        for entry in items:
            raw = entry.get("item", {})
            title = raw.get("name")
            bedrooms, location = parse_title(title) if title else (None, None)
            url = raw.get("url") or entry.get("url")
            if not url and title:
                queue = title_to_hrefs.get(title.strip().casefold())
                if queue:
                    url = queue.pop(0)
            listing = ScrapedListing(
                source_listing_id = raw.get("@id", "").split("/")[-1],
                url = url,
                title = raw.get("name"),
                price = raw.get("offers", {}).get("priceSpecification", {}).get("price"),
                bedrooms = bedrooms,
                location = location,
                image_urls = [raw.get("image", {}).get("url")] if raw.get("image") else [],
                raw = raw,
            )
            listings.append(listing)
        return listings

"""BuyRentKenya scraper (JSON-LD strategy).

The listings live in a <script type="application/ld+json"> block; the HTML
is only used to recover listing URLs the rental markup omits.

Source notes (buyrentkenya.com):
  - Laravel Livewire, server-side rendered.
  - Parse JSON-LD: @graph -> ItemList node -> itemListElement.
    BRK re-platformed on 2026-09-01: ItemList used to be nested under the
    WebPage node's mainEntity, and @type used to be a plain string rather
    than ["WebPage", "SearchResultsPage"]. _item_list_elements() reads
    both shapes; see the comment in parse() for what the change cost.
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
from backend.scraper.base import BaseScraper, ScrapedListing, ParserOutOfDate
from typing import Any, List, Optional, Tuple
import json
import re
from bs4 import BeautifulSoup


def _node_types(node: dict) -> List[str]:
    """JSON-LD allows @type to be a string OR a list of strings.

    BRK served "WebPage" before the 2026-09-01 re-platform and
    ["WebPage", "SearchResultsPage"] after. Comparing a string to a list
    silently matches nothing, which is how this broke.
    """
    raw = node.get("@type")
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [t for t in raw if isinstance(t, str)]
    return []


def _item_list_elements(graph: List[dict]) -> List[Any]:
    """Pull itemListElement out of @graph, handling both known shapes.

    Current (post-2026-09-01): ItemList is its own top-level @graph node.
    Legacy: ItemList was nested at WebPage.mainEntity.

    Both are tried because the old shape may still appear on some routes,
    and a parser that only understands today's markup breaks on the next
    change in the same silent way.
    """
    for node in graph:
        if not isinstance(node, dict):
            continue
        if "ItemList" in _node_types(node):
            items = node.get("itemListElement")
            if items:
                return items

    for node in graph:
        if not isinstance(node, dict):
            continue
        if "WebPage" in _node_types(node):
            main = node.get("mainEntity")
            if isinstance(main, dict):
                items = main.get("itemListElement")
                if items:
                    return items

    return []


# Card parsing. These match VISIBLE TEXT and the URL slug rather than CSS
# classes: BRK's new stack is Tailwind, so class names like
# "bg-surface-default" change with any design tweak, while "4 Bedrooms" and
# the slug do not.
CARD_PRICE_RE = re.compile(r"KSh\s*([\d,]+)", re.I)
CARD_BEDS_RE = re.compile(r"(\d+)\s*Bedrooms?\b", re.I)
CARD_BATHS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*Bathrooms?\b", re.I)
CARD_PLOT_RE = re.compile(r"[\d.]+\s*ac\s*Plot", re.I)

# "4-bedroom-townhouse-for-sale-buruburu-3672030"
SLUG_ID_RE = re.compile(r"-(\d+)$")
SLUG_BEDS_RE = re.compile(r"^(\d+)-bedroom", re.I)

# Uploaded photos are UUID-named; agency logos are not. The first <img> in
# a card is the agent's badge, so picking image_urls[0] blindly would hash
# the agency logo instead of the property — and a logo shared across an
# agency's listings is exactly what CK_GROUP_CAP exists to defend against.
#
# Anchored to the FILENAME, not the whole URL. Agency assets are served
# from /agencies/<uuid>/logo_....webp, so a URL-wide UUID search matches
# the agency's own id and cheerfully returns the logo — which is what the
# first version of this did, caught by test_hero_image_is_not_the_agency_logo.
IMG_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)

# Path or filename markers that mean "this is branding, not the property".
IMG_EXCLUDE = ("logo", "/agencies/", "placeholder", "watermark")

# h3 text that labels a section rather than naming the listing.
CARD_LABELS = {"description"}


# The new CDN appends a rendition suffix — the SAME photo arrives as
# "...jpeg_cardMobile.webp" or "...jpeg_medium.webp" depending on which
# card variant rendered it. cluster_key hashes image_url, so leaving that
# to chance would split identical listings on nothing but a breakpoint.
# Rewrite to one rendition rather than stripping the suffix: a bare
# "...jpeg" is not guaranteed to be fetchable, and the hasher has to be
# able to download whatever we store.
# The source extension is optional: most files are "<uuid>.jpeg_medium.webp"
# but some carry no extension at all ("4d8a61011490_cardMobile.webp"), and
# requiring one left those on whatever rendition happened to render.
IMG_RENDITION_RE = re.compile(
    r"((?:\.(?:jpe?g|jfif|png|webp))?)_[A-Za-z0-9]+\.webp$", re.I)
CANONICAL_RENDITION = r"\1_medium.webp"


def _canonical_image(src: str) -> str:
    return IMG_RENDITION_RE.sub(CANONICAL_RENDITION, src)


def _card_image(card) -> Optional[str]:
    """First image that looks like an uploaded photo, not agency branding.

    Exclusion runs BEFORE preference. Preferring a UUID first meant a logo
    at /agencies/<uuid>/logo_x.webp won on the strength of the agency's id.
    """
    candidates = []
    for img in card.select("img"):
        src = img.get("src") or img.get("data-src") or ""
        if not src:
            continue
        lowered = src.casefold()
        if any(marker in lowered for marker in IMG_EXCLUDE):
            continue
        candidates.append(src)

    for src in candidates:
        filename = src.rsplit("/", 1)[-1]
        if IMG_UUID_RE.match(filename):
            return _canonical_image(src)
    if candidates:
        return _canonical_image(candidates[0])
    return None


def _location_from_slug(slug: str) -> Optional[str]:
    """Last-resort area name: '...-for-sale-buruburu-3672030' -> 'Buruburu'."""
    parts = SLUG_ID_RE.sub("", slug).split("-")
    if not parts:
        return None
    tail = parts[-1]
    return tail.replace("_", " ").title() if tail else None


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

        # Take the first ld+json block that actually carries @graph rather
        # than blindly the first block — BRK now emits WebSite/Organization
        # nodes too, and a future extra block must not shadow the real one.
        data = None
        for block in soup.find_all("script", type="application/ld+json"):
            try:
                candidate = json.loads(block.string or "")
            except (ValueError, TypeError):
                continue
            if isinstance(candidate, dict) and candidate.get("@graph"):
                data = candidate
                break

        anchors = soup.select('a[href*="/listings/"]')

        if data is None:
            # No structured data at all. If the page is nonetheless showing
            # listing cards, the markup changed and we must not report this
            # as an empty page — see the guard note below.
            if anchors:
                raise ParserOutOfDate(
                    f"no ld+json @graph block, but {len(anchors)} listing "
                    "anchors are present")
            return []

        graph = data.get("@graph", [])
        items = _item_list_elements(graph)

        # 2026-09-01: BRK re-platformed. Two things changed together and
        # either alone was fatal:
        #   1. @type became a LIST — ["WebPage", "SearchResultsPage"] — so
        #      the old `node.get("@type") == "WebPage"` never matched.
        #   2. ItemList moved OUT of WebPage.mainEntity into its own
        #      top-level @graph node.
        # The old code returned [] on both, which the scraper read as "end
        # of pagination" and which would have staled all ~13,800 BRK
        # listings had MIN_SCRAPE_RATIO not held. _item_list_elements()
        # handles both shapes; this guard makes any future break loud.
        if not items and anchors:
            raise ParserOutOfDate(
                f"parsed 0 items from JSON-LD but the page shows "
                f"{len(anchors)} listing anchors — BRK markup has changed")

        # The ItemList now carries ONLY {@type, position, url} — no nested
        # Product — so every field below comes from the rendered card. The
        # JSON-LD is kept as a canary: it still states authoritatively how
        # many listings this page holds, so a mismatch against the cards we
        # can actually read means the markup moved again.
        cards = [art for art in soup.find_all("article")
                 if art.select_one('a[href*="/listings/"]')]

        if items and not cards:
            raise ParserOutOfDate(
                f"JSON-LD lists {len(items)} listings but no article cards "
                "were readable — BRK card markup has changed")

        listings = []
        for card in cards:
            href = ""
            for a in card.select('a[href*="/listings/"]'):
                href = (a.get("href") or "").split("?")[0]
                if href:
                    break
            if not href:
                continue
            url = self.base_url + href if href.startswith("/") else href

            # The slug is the most durable thing on the page: it carries the
            # listing id and the bed count regardless of how the card is
            # styled. Everything read out of the HTML below is checked
            # against it rather than trusted outright.
            slug = href.rstrip("/").split("/")[-1]
            id_match = SLUG_ID_RE.search(slug)
            # MUST stay "item-<digits>". The pre-replatform parser took this
            # from the JSON-LD @id, which was already "item-4052696", and
            # save_listings upserts on (source_id, source_listing_id).
            # Emitting a bare "4052696" would make all ~13,800 existing BRK
            # rows unmatchable: every listing inserts as new, every old row
            # goes unseen, and the stale pass buries the lot. The digits in
            # the slug are the same digits, so the old form is recoverable.
            listing_id = ("item-" + id_match.group(1)) if id_match else slug

            segments = [s.strip() for s in card.get_text("|").split("|")
                        if s.strip()]
            text = " ".join(segments)

            # Two h3s per card: a literal "Description" label and the real
            # title. Take the first that isn't a known label.
            title = None
            for h in card.select("h3, h2"):
                candidate = h.get_text(strip=True)
                if candidate and candidate.casefold() not in CARD_LABELS:
                    title = candidate
                    break

            # Location sits immediately after the title in the card's text
            # flow ("... With SQ | Buruburu, Nairobi | 4 | Bedrooms ...").
            location = None
            if title and title in segments:
                idx = segments.index(title)
                if idx + 1 < len(segments):
                    nxt = segments[idx + 1]
                    if nxt and not nxt.isdigit():
                        location = nxt
            if not location and title:
                _, location = parse_title(title)
            if not location:
                location = _location_from_slug(slug)

            price = None
            price_match = CARD_PRICE_RE.search(text)
            if price_match:
                try:
                    price = float(price_match.group(1).replace(",", ""))
                except ValueError:
                    price = None
            # Standing rule: POA is NULL, never 0. A card with no price
            # figure is POA, and a parsed 0 is meaningless for property.
            if price == 0:
                price = None

            bedrooms = None
            beds_match = CARD_BEDS_RE.search(text)
            if beds_match:
                bedrooms = int(beds_match.group(1))
            if bedrooms is None:
                slug_beds = SLUG_BEDS_RE.match(slug)
                if slug_beds:
                    bedrooms = int(slug_beds.group(1))

            baths = None
            baths_match = CARD_BATHS_RE.search(text)
            if baths_match:
                try:
                    baths = float(baths_match.group(1))
                except ValueError:
                    baths = None

            image_urls = []
            hero = _card_image(card)
            if hero:
                image_urls.append(hero)

            listings.append(ScrapedListing(
                source_listing_id=listing_id,
                url=url,
                title=title,
                price=price,
                bedrooms=bedrooms,
                location=location,
                baths=baths,
                # size_sqm is deliberately left None. The card shows plot
                # acreage ("0.12 ac Plot"), which is land area, not the
                # floor area P24 reports. Converting one into the other
                # would corrupt both cluster_key and the scorer's size
                # comparison with a number that means something different.
                # The raw string is kept below if it's ever wanted.
                size_sqm=None,
                image_urls=image_urls,
                raw={
                    "slug": slug,
                    "card_text": text[:2000],
                    "plot": (CARD_PLOT_RE.search(text).group(0)
                             if CARD_PLOT_RE.search(text) else None),
                },
            ))
        return listings

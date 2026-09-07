"""Consumer-facing HTML pages (server-rendered Jinja2 → SEO-friendly).

Separate from the demo JSON API on purpose: /properties etc. stay
untouched; these routes serve buyers. Legal-by-design rules apply here:
facts only, always link out to the source site, never rehost images
(image URLs are hotlinked with a graceful fallback).
"""
import math
import os
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from backend.db.client import get_client

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory="backend/web/templates")

# Absolute base URL for canonicals and OG tags (crawlers and link
# unfurlers want absolute URLs, not paths)
templates.env.globals["BASE_URL"] = \
    os.getenv("SITE_BASE_URL", "http://localhost:8000").rstrip("/")

PER_PAGE = 24
SOURCE_NAMES = {1: "BuyRentKenya", 2: "Property24"}


def format_price(value) -> str:
    """8500000 → 'KSh 8,500,000'; None → 'Price on application'."""
    if value is None:
        return "Price on application"
    return f"KSh {int(value):,}"


templates.env.filters["price"] = format_price


def format_price_range(p) -> str:
    """Single price when sources agree, 'KSh X – Y' when they don't.
    Rent prices are monthly and say so."""
    suffix = " /month" if p.get("category") == "rent" else ""
    lo, hi = p.get("price_min"), p.get("price_max")
    if lo and hi and lo != hi:
        return f"KSh {int(lo):,} – {int(hi):,}{suffix}"
    base = format_price(p.get("price") or lo)
    return base + suffix if base[0] == "K" else base  # no suffix on POA


templates.env.filters["price_range"] = format_price_range


def format_beds_range(p) -> str:
    """Single bed count when sources agree, '4–5 bed' when they don't.
    Returns '' when unknown so the card meta can compose separators."""
    lo, hi = p.get("beds_min"), p.get("beds_max")
    if lo and hi and lo != hi:
        return f"{int(lo)}–{int(hi)} bed"
    base = p.get("bedrooms") or lo
    return f"{int(base)} bed" if base else ""


def format_baths_range(p) -> str:
    """Single bath count when sources agree, '2–3 bath' when they don't.
    Returns '' when unknown so the card meta can compose separators."""
    lo, hi = p.get("baths_min"), p.get("baths_max")
    if lo and hi and lo != hi:
        return f"{int(lo)}–{int(hi)} bath"
    base = p.get("bathrooms") or lo
    return f"{int(base)} bath" if base else ""


templates.env.filters["beds_range"] = format_beds_range
templates.env.filters["baths_range"] = format_baths_range


# ── suburb URL slugs ─────────────────────────────────────────────────
#
# Suburb values are stored lowercase with spaces ("ngong road"), and the
# segment URLs were originally built by dropping that value straight into
# the path — which produced "/houses-for-sale/ngong road". Browsers
# %20-encode it and it *works*, but Google indexed the space form (seen in
# Search Console), the canonical tag emitted a raw space, and the URLs are
# unshareable. Slug is the hyphen form; the resolver accepts both so the
# already-indexed space URLs still resolve (and get 301'd — see segments).

_SLUG_SEP = re.compile(r"[^a-z0-9]+")


def suburb_slug(suburb: str) -> str:
    """DB suburb value → URL slug. 'ngong road' → 'ngong-road'."""
    return _SLUG_SEP.sub("-", (suburb or "").lower()).strip("-")


def suburb_from_slug(slug: str) -> str:
    """URL path segment → DB suburb value. Accepts the hyphen form, the
    legacy space form and any casing: 'Ngong-Road', 'ngong road' →
    'ngong road'."""
    return _SLUG_SEP.sub(" ", (slug or "").lower()).strip()


templates.env.filters["suburb_slug"] = suburb_slug


def days_listed(first_seen) -> Optional[int]:
    """Days since we first saw this advert, or None if unknown.

    NOT the same as days on the market: first_seen is when *we* first
    crawled it, so anything that existed before 29 June 2026 reads as the
    age of the corpus, not of the listing. The badge says "seen for" and
    not "on the market for" precisely because of that, and it gets more
    truthful every day the crawler runs.

    Nobody else in this market shows it at all. Portals are paid by the
    agent, and an advert visibly sitting unsold for two months is bad for
    the person paying — which is exactly why it is useful to a buyer.
    """
    if not first_seen:
        return None
    try:
        seen = datetime.fromisoformat(str(first_seen).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    days = (datetime.now(timezone.utc) - seen).days
    # Clock skew or a future timestamp shouldn't render "-1 days ago".
    return days if days >= 0 else None


templates.env.filters["days_listed"] = days_listed


def _opt_int(value: Optional[str]) -> Optional[int]:
    """HTML forms submit empty fields as '' — treat those (and junk)
    as 'no filter' instead of letting FastAPI 422 the whole request."""
    if value is None or value.strip() == "":
        return None
    try:
        return max(0, int(value))
    except ValueError:
        return None


# Sort key → (column, descending). "newest" stays the default: it is what
# the site has always done, and price sorts put POA rows (price IS NULL)
# at one end regardless, so it should not be the first thing a visitor
# meets. Every option is an indexed column — no computed sorts.
SORTS = {
    "newest":     ("first_seen", True),
    "price_asc":  ("price", False),
    "price_desc": ("price", True),
}
DEFAULT_SORT = "newest"


# ── Mislisted rentals on sale pages ────────────────────────────────────
#
# Agents post rentals under "For Sale" and the portals publish them as
# submitted. A monthly rent is ~100x smaller than a sale price, so every
# one of these sorts to the TOP of a cheapest-first sale page — the first
# thing a buyer sees, and where every paid click lands.
#
# Verified on Property24, 2026-09-07: "5 Bedroom House for Sale in
# Lavington", KSh 500 000, description "Huge house for rent in lavinton".
# P24 genuinely categorises it for-sale because that is how it was
# submitted. Our data is faithful; the source is wrong.
#
# Measured on the 7 Sep corpus (13,859 active sale properties):
#   - 17 rows priced 500,000-1,290,000, ALL 4-6 bed, in Karen, Runda,
#     Muthaiga, Nyari, Lavington, Kitisuru, Thigiri and Loresho. Two say
#     "for rent" in their own titles.
#   - the cheapest GENUINE sale is a 1.75M studio in Garden Estate.
# Nothing sits between 1.29M and 1.75M, so the boundary is unambiguous.
#
# Per-bedroom 5th percentiles, for scale: 1-bed 3M, 2-bed 6M, 3-bed 9M,
# 4-bed 13M, 5-bed 30M, 6-bed 35M. The suppressed rows are 26-60x below
# the 5th percentile for their own bed count.
SALE_PRICE_FLOOR = 1_500_000          # nothing legitimate below this
SALE_FLOOR_LARGE_HOME = 3_000_000     # ...and 4+ beds cannot be this cheap
SALE_FLOOR_LARGE_BEDS = 4

# 3M, not 5M. The mislisted rentals top out at 1,290,000, so 3M keeps a
# 2.3x margin over the worst of them while sparing a real listing that 5M
# caught: "Modern Studio, 1, 2, 3 & 4 Bedroom Apartments" in Kilimani at
# KSh 4,600,000.
#
# That row is a shape worth knowing about. A development advertising a
# RANGE of unit types gets price = the entry (studio) price and bedrooms =
# the largest unit named in the title, so it reads "4 bed at 4.6M" and
# looks impossible. The listing is genuine; the two fields just describe
# different units. Any threshold on price-per-bedroom will keep meeting
# these, and the fix is eventually to record a price range for
# multi-unit listings rather than to keep tuning floors.


def apply_sale_price_floor(q, category: str):
    """Hide sale rows whose price is implausible for a sale.

    NOT a data mutation. `category` is what the source told us, and we have
    no evidence it is wrong — only that the price is implausible for it.
    Same stance as ADR 0010: suppress at display time, leave the row
    intact, and keep the property page resolving for direct links.

    Rent is untouched. 500,000/month is an ordinary Nairobi rent, which is
    exactly why these rows are invisible until they reach a sale page.
    """
    if category != "sale":
        return q
    return q.or_(
        f"price.is.null,"
        f"and(price.gte.{SALE_PRICE_FLOOR},bedrooms.lt.{SALE_FLOOR_LARGE_BEDS}),"
        f"and(price.gte.{SALE_FLOOR_LARGE_HOME},bedrooms.gte.{SALE_FLOOR_LARGE_BEDS}),"
        f"and(price.gte.{SALE_PRICE_FLOOR},bedrooms.is.null)"
    )


@router.get("/")
def home(
    request: Request,
    location: Optional[str] = Query(None),
    bedrooms: Optional[str] = Query(None),
    min_price: Optional[str] = Query(None),
    max_price: Optional[str] = Query(None),
    category: str = Query("sale"),
    sort: str = Query(DEFAULT_SORT),
    page: int = Query(1, ge=1),
):
    """Search page — also the homepage. Server-rendered results."""
    db = get_client()

    bedrooms = _opt_int(bedrooms)
    min_price = _opt_int(min_price)
    max_price = _opt_int(max_price)
    if category not in ("sale", "rent"):
        category = "sale"
    if sort not in SORTS:
        sort = DEFAULT_SORT

    q = (db.table("properties")
         .select("title, slug, price, price_min, price_max, location, "
                 "bedrooms, bathrooms, beds_min, beds_max, baths_min, "
                 "baths_max, size_sqm, image_url, source_count, "
                 "category, first_seen", count="exact")
         .eq("status", "active")
         .eq("category", category))

    if location:
        q = q.ilike("location", f"%{location.strip()}%")
    if bedrooms is not None:
        q = q.gte("bedrooms", bedrooms)  # UI offers "n+ bed"
    if min_price is not None:
        q = q.gte("price", min_price)
    if max_price is not None:
        q = q.lte("price", max_price)

    offset = (page - 1) * PER_PAGE
    # Mislisted rentals (agents posting a monthly rent under "For Sale")
    # are ~100x below a real sale price, so they fill page 1 of a
    # cheapest-first sale page. See segments.apply_sale_price_floor.
    q = apply_sale_price_floor(q, category)

    sort_col, sort_desc = SORTS[sort]
    if sort_col == "price":
        # POA rows (price IS NULL) sort to the top of an ascending list in
        # Postgres NULLS-LAST terms and would otherwise fill page 1 with
        # properties that show no price at all. Someone who asked to sort
        # by price wants prices.
        q = q.not_.is_("price", "null")
    result = (q.order(sort_col, desc=sort_desc)
              .order("id", desc=True)  # tiebreak for same-crawl batches
              .range(offset, offset + PER_PAGE - 1)
              .execute())

    total = result.count or 0
    total_pages = max(1, math.ceil(total / PER_PAGE))

    # Live trust numbers for the hero strip ("X adverts → Y real
    # properties"). Defensive: never 500 the homepage over decoration.
    trust = None
    try:
        n_listings = (db.table("listings").select("id", count="exact")
                      .eq("status", "active").limit(1).execute()).count or 0
        n_props = (db.table("properties").select("id", count="exact")
                   .eq("status", "active").limit(1).execute()).count or 0
        if n_listings and n_props and n_listings > n_props:
            trust = {
                "listings": n_listings,
                "properties": n_props,
                "merged": n_listings - n_props,
            }
    except Exception:
        pass

    # Crawlable path into the segment pages. Defensive: the view only
    # exists after seo_support.sql has run — never 500 the homepage.
    try:
        top_suburbs = (db.table("suburb_stats")
                       .select("suburb, property_count")
                       .eq("category", category)
                       .eq("is_suburb_total", True)
                       .gte("property_count", 5)
                       .order("property_count", desc=True)
                       .limit(12)
                       .execute()).data
    except Exception:
        top_suburbs = []

    return templates.TemplateResponse(request, "home.html", {
        "properties": result.data,
        "total": total,
        "page": page,
        "total_pages": total_pages,
        "top_suburbs": top_suburbs,
        "trust": trust,
        "suburb_prefix": "apartments-for-rent" if category == "rent"
                         else "houses-for-sale",
        # Search-result views must not be indexed. Google found
        # "/?page=8&category=sale&location=" and spent crawl budget on it
        # — on a site with 810 sitemap URLs and 5 total clicks, attention
        # spent on page 8 of a filtered homepage is attention not spent on
        # the property pages that actually earn those clicks.
        #
        # Filter combinations are effectively infinite and near-duplicate,
        # which is the classic faceted-navigation trap. The intended SEO
        # surface is /houses-for-sale/{suburb}[/{n}-bedroom], not this.
        #
        # noindex,FOLLOW, not noindex,nofollow: the links through to
        # property pages should still be crawled. And the category roots
        # (/ and /?category=rent) stay indexable — they're real pages.
        "noindex": bool(location or bedrooms is not None
                        or min_price is not None or max_price is not None
                        or page > 1 or sort != DEFAULT_SORT),
        "canonical": f"/?category={category}" if category == "rent" else "/",
        "filters": {
            "location": location or "",
            "bedrooms": bedrooms,
            "min_price": min_price,
            "max_price": max_price,
            "category": category,
            "sort": sort,
        },
    })


@router.get("/property/{slug}")
def property_page(request: Request, slug: str):
    """Property detail: facts + link out to every source listing.

    Slug resolution: canonical hit → 200. Retired slug → follow the
    redirect chain (max 5 hops) → 301 to canonical. Unknown → 404.
    """
    db = get_client()

    prop = (db.table("properties").select("*")
            .eq("slug", slug).limit(1).execute())

    if not prop.data:
        seen = {slug}
        current = slug
        for _ in range(5):
            row = (db.table("property_slugs").select("target_slug")
                   .eq("slug", current).limit(1).execute())
            if not row.data:
                raise HTTPException(status_code=404, detail="Property not found")
            current = row.data[0]["target_slug"]
            hit = (db.table("properties").select("id")
                   .eq("slug", current).limit(1).execute())
            if hit.data:
                return RedirectResponse(f"/property/{current}", status_code=301)
            if current in seen:
                break  # circular chain — treat as gone
            seen.add(current)
        raise HTTPException(status_code=404, detail="Property not found")

    p = prop.data[0]

    listings = (db.table("listings")
                .select("source_id, url, title, price, status, last_seen")
                .eq("property_id", p["id"])
                .order("source_id")
                .execute())

    sources = []
    for l in listings.data:
        sources.append({
            "name": SOURCE_NAMES.get(l["source_id"], "Source"),
            "url": l["url"],
            "price": l["price"],
            "active": l["status"] == "active",
        })

    return templates.TemplateResponse(request, "property.html", {
        "p": p,
        "sources": sources,
    })

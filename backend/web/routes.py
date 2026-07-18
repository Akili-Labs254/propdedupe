"""Consumer-facing HTML pages (server-rendered Jinja2 → SEO-friendly).

Separate from the demo JSON API on purpose: /properties etc. stay
untouched; these routes serve buyers. Legal-by-design rules apply here:
facts only, always link out to the source site, never rehost images
(image URLs are hotlinked with a graceful fallback).
"""
import math
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from backend.db.client import get_client

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory="backend/web/templates")

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


def _opt_int(value: Optional[str]) -> Optional[int]:
    """HTML forms submit empty fields as '' — treat those (and junk)
    as 'no filter' instead of letting FastAPI 422 the whole request."""
    if value is None or value.strip() == "":
        return None
    try:
        return max(0, int(value))
    except ValueError:
        return None


@router.get("/")
def home(
    request: Request,
    location: Optional[str] = Query(None),
    bedrooms: Optional[str] = Query(None),
    min_price: Optional[str] = Query(None),
    max_price: Optional[str] = Query(None),
    category: str = Query("sale"),
    page: int = Query(1, ge=1),
):
    """Search page — also the homepage. Server-rendered results."""
    db = get_client()

    bedrooms = _opt_int(bedrooms)
    min_price = _opt_int(min_price)
    max_price = _opt_int(max_price)
    if category not in ("sale", "rent"):
        category = "sale"

    q = (db.table("properties")
         .select("title, slug, price, price_min, price_max, location, "
                 "bedrooms, bathrooms, size_sqm, image_url, source_count, "
                 "category", count="exact")
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
    result = (q.order("first_seen", desc=True)
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
        "filters": {
            "location": location or "",
            "bedrooms": bedrooms,
            "min_price": min_price,
            "max_price": max_price,
            "category": category,
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

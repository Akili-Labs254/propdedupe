"""SEO segment pages, parametrized by category:

    /houses-for-sale/{suburb}[/{n}-bedroom]
    /apartments-for-rent/{suburb}[/{n}-bedroom]

The long-tail traffic play. Every page is server-rendered with computed
stats (count, price range, median) so it's genuinely more useful than
the portals' generic filter pages — and only pages with enough inventory
exist at all (MIN_SEGMENT), so we never serve Google thin content. Rent
pages 404 until rental data lands, then appear automatically.

Internal links come from the dedup engine's suburb adjacency graph:
each suburb page links its qualifying neighbours.
"""
import math
import os
import re
import time
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, RedirectResponse, Response

from backend.db.client import get_client
from backend.dedup.adjacency import ADJACENCY
from backend.web.routes import (  # shared env + filters + sort options
    DEFAULT_SORT, SORTS, suburb_from_slug, suburb_slug, templates,
)

router = APIRouter(include_in_schema=False)

MIN_SEGMENT = 5     # pages below this many active properties don't exist
PER_PAGE = 24
BASE_URL = os.getenv("SITE_BASE_URL", "http://localhost:8000").rstrip("/")

# Sitemap scope. Google rations crawling by site authority, and a new
# domain with no backlinks gets very little. Submitting everything
# spends that budget on the wrong pages: at 2026-07-29 Search Console
# reported 1.13K indexed against 28,249 "Discovered - currently not
# indexed" — i.e. Google saw the URLs and declined to crawl them.
#
# The corpus is ~452 segment pages vs ~28,415 property pages, so 98% of
# the sitemap was thin, largely portal-duplicative detail pages while the
# segment pages — the ones carrying unique aggregated data, and the ones
# actually ranking (position ~61 for "houses for sale in nairobi") — were
# 2% of it.
#
# So the sitemap now carries segments plus only the multi-source
# properties (~355): those are the pages that demonstrate the whole point
# of the product, and are the least duplicative of any single source.
# Single-source property pages stay fully crawlable via internal links
# from their segment pages — they're just no longer pushed at Google.
#
# Revisit once the domain has real backlinks and crawl budget to spend.
SITEMAP_MULTI_SOURCE_ONLY = True

_BEDS_RE = re.compile(r"^([1-8])-bedroom$")

# URL prefix + copy per category. Adding a category = one entry here
# (plus scraper URLs) — everything else derives from it.
# NOTE the mismatch between prefix and plural on "sale". The URL prefix
# is frozen: ~810 pages are indexed under /houses-for-sale/, and
# re-prefixing would mean 301-ing all of them mid-SEO-ramp for a cosmetic
# gain. The COPY is not frozen, and had to change on 2026-08-17: ingesting
# BRK apartments-for-sale put 6,060 flats under pages titled "Houses for
# Sale in X". Title/content mismatch is a real ranking signal, and it was
# simply untrue.
#
# "Homes" over "Property" because it stays close to the keyword the pages
# already rank for ("houses for sale <suburb>") while covering flats
# honestly — "homes for sale" is a searched term in its own right.
CATEGORIES = {
    "sale": {"prefix": "houses-for-sale", "plural": "Homes",
             "action": "for Sale"},
    "rent": {"prefix": "apartments-for-rent", "plural": "Apartments",
             "action": "for Rent"},
}


def display_name(suburb: str) -> str:
    return suburb.title()


# Slug → suburb for names a plain hyphen↔space swap can't reverse (a
# suburb containing punctuation, e.g. "st.mary road" → "st-mary-road").
# None exist today, but normalize_suburb() can emit them, and the failure
# mode would be a silent 404. Cached because the list is ~80 rows and only
# changes on a dedup rebuild (3×/week) — the free-tier box is IO-bound.
_SLUG_CACHE = {}            # category → (expires_at, {slug: suburb})
_SLUG_CACHE_TTL = 600       # seconds


def suburb_for_slug(db, slug: str, category: str) -> Optional[str]:
    now = time.monotonic()
    entry = _SLUG_CACHE.get(category)
    if not entry or entry[0] < now:
        rows = (db.table("suburb_stats")
                .select("suburb")
                .eq("category", category)
                .eq("is_suburb_total", True)
                .gte("property_count", 1)   # NOT MIN_SEGMENT — thin suburbs
                .execute())                 # still serve, see _segment_page
        mapping = {suburb_slug(r["suburb"]): r["suburb"] for r in rows.data}
        entry = _SLUG_CACHE[category] = (now + _SLUG_CACHE_TTL, mapping)
    return entry[1].get(slug)


def suburb_totals(db, category: str) -> List[dict]:
    """All suburb-wide stat rows meeting the threshold, biggest first."""
    rows = (db.table("suburb_stats")
            .select("*")
            .eq("category", category)
            .eq("is_suburb_total", True)
            .gte("property_count", MIN_SEGMENT)
            .order("property_count", desc=True)
            .execute())
    return rows.data


def segment_stats(db, suburb: str, category: str,
                  bedrooms: Optional[int] = None) -> Optional[dict]:
    q = (db.table("suburb_stats").select("*")
         .eq("suburb", suburb).eq("category", category))
    if bedrooms is None:
        q = q.eq("is_suburb_total", True)
    else:
        q = q.eq("is_suburb_total", False).eq("bedrooms", bedrooms)
    rows = q.limit(1).execute()
    return rows.data[0] if rows.data else None


def fetch_properties(db, suburb: str, category: str,
                     bedrooms: Optional[int], page: int,
                     sort: str = DEFAULT_SORT):
    q = (db.table("properties")
         .select("title, slug, price, price_min, price_max, location, "
                 "bedrooms, bathrooms, beds_min, beds_max, baths_min, "
                 "baths_max, size_sqm, image_url, source_count, "
                 "category, first_seen", count="exact")
         .eq("status", "active")
         .eq("category", category)
         .eq("suburb", suburb))
    if bedrooms is not None:
        q = q.eq("bedrooms", bedrooms)

    sort_col, sort_desc = SORTS.get(sort, SORTS[DEFAULT_SORT])
    if sort_col == "price":
        # Same rule as the home route: someone who asked to sort by price
        # does not want a page of "Price on application".
        q = q.not_.is_("price", "null")

    offset = (page - 1) * PER_PAGE
    return (q.order(sort_col, desc=sort_desc)
            .order("id", desc=True)
            .range(offset, offset + PER_PAGE - 1)
            .execute())


def qualifying_neighbours(db, suburb: str, category: str) -> List[dict]:
    """Adjacent suburbs (from the dedup adjacency graph) that have their
    own segment page — the internal link mesh."""
    neighbours = sorted(ADJACENCY.get(suburb, ()))
    if not neighbours:
        return []
    rows = (db.table("suburb_stats")
            .select("suburb, property_count")
            .eq("category", category)
            .eq("is_suburb_total", True)
            .gte("property_count", MIN_SEGMENT)
            .in_("suburb", neighbours)
            .order("property_count", desc=True)
            .execute())
    return rows.data


def _suburbs_index(request: Request, category: str):
    cfg = CATEGORIES[category]
    db = get_client()
    return templates.TemplateResponse(request, "suburbs.html", {
        "suburbs": suburb_totals(db, category),
        "display_name": display_name,
        "category": category,
        "cfg": cfg,
    })


def _segment_page(request: Request, suburb: str, category: str,
                  bedrooms: Optional[int], page: int,
                  sort: str = DEFAULT_SORT):
    if sort not in SORTS:
        sort = DEFAULT_SORT
    cfg = CATEGORIES[category]
    raw, suburb = suburb, suburb_from_slug(suburb)
    slug = suburb_slug(suburb)

    # Canonicalise the URL before touching the DB. Google indexed the old
    # space form ("/houses-for-sale/kiambu road") and mixed casing; both
    # 301 to the hyphen form so the crawl equity follows. Cheap: no query.
    if raw != slug:
        target = f"/{cfg['prefix']}/{slug}"
        if bedrooms:
            target += f"/{bedrooms}-bedroom"
        if request.url.query:
            target += f"?{request.url.query}"
        return RedirectResponse(target, status_code=301)

    db = get_client()

    stats = segment_stats(db, suburb, category, bedrooms)
    if stats is None:
        alt = suburb_for_slug(db, slug, category)
        if alt and alt != suburb:
            suburb = alt
            stats = segment_stats(db, suburb, category, bedrooms)
    if not stats or not stats.get("property_count"):
        raise HTTPException(status_code=404, detail="Not found")

    # Segment pages used to 404 below MIN_SEGMENT. But inventory oscillates
    # across that line, so a page Google indexed while it had 5 properties
    # would 404 once it dropped to 4 — and a 404 on an indexed URL is a hard
    # signal to drop it. 2026-08: our single best search click landed on
    # /apartments-for-rent/city-park-drive and got a 404; it has 6 properties
    # again now. 978 segment pages currently sit below the line.
    #
    # So the threshold no longer decides whether the page EXISTS, only
    # whether it's indexable. Thin pages serve (a real page beats a dead end
    # for anyone arriving from an old SERP) but carry noindex, and are still
    # excluded from the sitemap, the suburb index and the nearby-suburb links
    # — so nothing new gets discovered through us.
    thin = stats["property_count"] < MIN_SEGMENT

    result = fetch_properties(db, suburb, category, bedrooms, page, sort)
    total = result.count or 0
    total_pages = max(1, math.ceil(total / PER_PAGE))

    # Bed-count sub-pages that qualify (only shown on the suburb page)
    bed_links = []
    if bedrooms is None:
        rows = (db.table("suburb_stats")
                .select("bedrooms, property_count")
                .eq("suburb", suburb)
                .eq("category", category)
                .eq("is_suburb_total", False)
                .gte("property_count", MIN_SEGMENT)
                .not_.is_("bedrooms", "null")
                .order("bedrooms")
                .execute())
        bed_links = [r for r in rows.data if 1 <= (r["bedrooms"] or 0) <= 8]

    return templates.TemplateResponse(request, "segment.html", {
        "suburb": suburb,
        "suburb_name": display_name(suburb),
        "bedrooms": bedrooms,
        "sort": sort,
        "stats": stats,
        "properties": result.data,
        "total": total,
        "page": page,
        "total_pages": total_pages,
        "bed_links": bed_links,
        "neighbours": qualifying_neighbours(db, suburb, category),
        "display_name": display_name,
        "category": category,
        "cfg": cfg,
        "thin": thin,
    })


def _parse_beds(beds_segment: str) -> int:
    m = _BEDS_RE.match(beds_segment)
    if not m:
        raise HTTPException(status_code=404, detail="Not found")
    return int(m.group(1))


@router.get("/houses-for-sale")
def sale_index(request: Request):
    return _suburbs_index(request, "sale")


@router.get("/apartments-for-rent")
def rent_index(request: Request):
    return _suburbs_index(request, "rent")


@router.get("/houses-for-sale/{suburb}")
def sale_suburb(request: Request, suburb: str, page: int = Query(1, ge=1),
                sort: str = Query(DEFAULT_SORT)):
    return _segment_page(request, suburb, "sale", None, page, sort)


@router.get("/apartments-for-rent/{suburb}")
def rent_suburb(request: Request, suburb: str, page: int = Query(1, ge=1),
                sort: str = Query(DEFAULT_SORT)):
    return _segment_page(request, suburb, "rent", None, page, sort)


@router.get("/houses-for-sale/{suburb}/{beds_segment}")
def sale_suburb_beds(request: Request, suburb: str, beds_segment: str,
                     page: int = Query(1, ge=1),
                     sort: str = Query(DEFAULT_SORT)):
    return _segment_page(request, suburb, "sale",
                         _parse_beds(beds_segment), page, sort)


@router.get("/apartments-for-rent/{suburb}/{beds_segment}")
def rent_suburb_beds(request: Request, suburb: str, beds_segment: str,
                     page: int = Query(1, ge=1),
                     sort: str = Query(DEFAULT_SORT)):
    return _segment_page(request, suburb, "rent",
                         _parse_beds(beds_segment), page, sort)


# ── sitemap + robots ─────────────────────────────────────────────────

@router.get("/sitemap.xml")
def sitemap():
    db = get_client()
    urls = [f"{BASE_URL}/"]

    for category, cfg in CATEGORIES.items():
        totals = suburb_totals(db, category)
        if not totals:
            continue  # category not launched yet — no empty index page
        prefix = cfg["prefix"]
        urls.append(f"{BASE_URL}/{prefix}")
        for row in totals:
            urls.append(f"{BASE_URL}/{prefix}/{suburb_slug(row['suburb'])}")

        beds_rows = (db.table("suburb_stats")
                     .select("suburb, bedrooms")
                     .eq("category", category)
                     .eq("is_suburb_total", False)
                     .gte("property_count", MIN_SEGMENT)
                     .not_.is_("bedrooms", "null")
                     .execute())
        for row in beds_rows.data:
            b = row["bedrooms"]
            if 1 <= b <= 8:
                urls.append(f"{BASE_URL}/{prefix}/"
                            f"{suburb_slug(row['suburb'])}/{b}-bedroom")

    offset = 0
    while True:
        q = (db.table("properties")
             .select("slug")
             .eq("status", "active")
             .not_.is_("slug", "null"))
        if SITEMAP_MULTI_SOURCE_ONLY:
            q = q.gt("source_count", 1)
        batch = (q.order("id")
                 .range(offset, offset + 999)
                 .execute())
        if not batch.data:
            break
        for row in batch.data:
            urls.append(f"{BASE_URL}/property/{row['slug']}")
        if len(batch.data) < 1000:
            break
        offset += 1000

    body = ['<?xml version="1.0" encoding="UTF-8"?>',
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u in urls:
        body.append(f"  <url><loc>{u}</loc></url>")
    body.append("</urlset>")
    return Response("\n".join(body), media_type="application/xml")


@router.get("/robots.txt")
def robots():
    return PlainTextResponse(
        "User-agent: *\n"
        "Disallow: /review\n"
        "Disallow: /admin\n"
        "Disallow: /docs\n"
        "Disallow: /health\n"
        f"Sitemap: {BASE_URL}/sitemap.xml\n"
    )

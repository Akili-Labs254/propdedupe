"""SEO segment pages: /houses-for-sale/{suburb}[/{n}-bedroom].

The long-tail traffic play. Every page is server-rendered with computed
stats (count, price range, median) so it's genuinely more useful than
the portals' generic filter pages — and only pages with enough inventory
exist at all (MIN_SEGMENT), so we never serve Google thin content.

Internal links come from the dedup engine's suburb adjacency graph:
each suburb page links its qualifying neighbours.
"""
import math
import os
import re
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, Response

from backend.db.client import get_client
from backend.dedup.adjacency import ADJACENCY
from backend.web.routes import templates  # shared env + filters

router = APIRouter(include_in_schema=False)

MIN_SEGMENT = 5     # pages below this many active properties don't exist
PER_PAGE = 24
BASE_URL = os.getenv("SITE_BASE_URL", "http://localhost:8000").rstrip("/")

_BEDS_RE = re.compile(r"^([1-8])-bedroom$")


def display_name(suburb: str) -> str:
    return suburb.title()


def suburb_totals(db) -> List[dict]:
    """All suburb-wide stat rows meeting the threshold, biggest first."""
    rows = (db.table("suburb_stats")
            .select("*")
            .eq("category", "sale")  # rent pages arrive with the rent data
            .eq("is_suburb_total", True)
            .gte("property_count", MIN_SEGMENT)
            .order("property_count", desc=True)
            .execute())
    return rows.data


def segment_stats(db, suburb: str, bedrooms: Optional[int] = None) -> Optional[dict]:
    q = (db.table("suburb_stats").select("*")
         .eq("suburb", suburb).eq("category", "sale"))
    if bedrooms is None:
        q = q.eq("is_suburb_total", True)
    else:
        q = q.eq("is_suburb_total", False).eq("bedrooms", bedrooms)
    rows = q.limit(1).execute()
    return rows.data[0] if rows.data else None


def fetch_properties(db, suburb: str, bedrooms: Optional[int], page: int):
    q = (db.table("properties")
         .select("title, slug, price, price_min, price_max, location, "
                 "bedrooms, bathrooms, size_sqm, image_url, source_count",
                 count="exact")
         .eq("status", "active")
         .eq("category", "sale")
         .eq("suburb", suburb))
    if bedrooms is not None:
        q = q.eq("bedrooms", bedrooms)
    offset = (page - 1) * PER_PAGE
    return (q.order("first_seen", desc=True)
            .order("id", desc=True)
            .range(offset, offset + PER_PAGE - 1)
            .execute())


def qualifying_neighbours(db, suburb: str) -> List[dict]:
    """Adjacent suburbs (from the dedup adjacency graph) that have their
    own segment page — the internal link mesh."""
    neighbours = sorted(ADJACENCY.get(suburb, ()))
    if not neighbours:
        return []
    rows = (db.table("suburb_stats")
            .select("suburb, property_count")
            .eq("category", "sale")
            .eq("is_suburb_total", True)
            .gte("property_count", MIN_SEGMENT)
            .in_("suburb", neighbours)
            .order("property_count", desc=True)
            .execute())
    return rows.data


@router.get("/houses-for-sale")
def suburbs_index(request: Request):
    db = get_client()
    suburbs = suburb_totals(db)
    return templates.TemplateResponse(request, "suburbs.html", {
        "suburbs": suburbs,
        "display_name": display_name,
    })


@router.get("/houses-for-sale/{suburb}")
def suburb_page(request: Request, suburb: str, page: int = Query(1, ge=1)):
    return _segment_page(request, suburb, None, page)


@router.get("/houses-for-sale/{suburb}/{beds_segment}")
def suburb_beds_page(request: Request, suburb: str, beds_segment: str,
                     page: int = Query(1, ge=1)):
    m = _BEDS_RE.match(beds_segment)
    if not m:
        raise HTTPException(status_code=404, detail="Not found")
    return _segment_page(request, suburb, int(m.group(1)), page)


def _segment_page(request: Request, suburb: str, bedrooms: Optional[int],
                  page: int):
    suburb = suburb.lower().strip()
    db = get_client()

    stats = segment_stats(db, suburb, bedrooms)
    if not stats or stats["property_count"] < MIN_SEGMENT:
        raise HTTPException(status_code=404, detail="Not found")

    result = fetch_properties(db, suburb, bedrooms, page)
    total = result.count or 0
    total_pages = max(1, math.ceil(total / PER_PAGE))

    # Bed-count sub-pages that qualify (only shown on the suburb page)
    bed_links = []
    if bedrooms is None:
        rows = (db.table("suburb_stats")
                .select("bedrooms, property_count")
                .eq("suburb", suburb)
                .eq("category", "sale")
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
        "stats": stats,
        "properties": result.data,
        "total": total,
        "page": page,
        "total_pages": total_pages,
        "bed_links": bed_links,
        "neighbours": qualifying_neighbours(db, suburb),
        "display_name": display_name,
    })


# ── sitemap + robots ─────────────────────────────────────────────────

@router.get("/sitemap.xml")
def sitemap():
    db = get_client()
    urls = [f"{BASE_URL}/", f"{BASE_URL}/houses-for-sale"]

    for row in suburb_totals(db):
        urls.append(f"{BASE_URL}/houses-for-sale/{row['suburb']}")

    beds_rows = (db.table("suburb_stats")
                 .select("suburb, bedrooms")
                 .eq("category", "sale")
                 .eq("is_suburb_total", False)
                 .gte("property_count", MIN_SEGMENT)
                 .not_.is_("bedrooms", "null")
                 .execute())
    for row in beds_rows.data:
        b = row["bedrooms"]
        if 1 <= b <= 8:
            urls.append(f"{BASE_URL}/houses-for-sale/{row['suburb']}/{b}-bedroom")

    offset = 0
    while True:
        batch = (db.table("properties")
                 .select("slug")
                 .eq("status", "active")
                 .not_.is_("slug", "null")
                 .order("id")
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

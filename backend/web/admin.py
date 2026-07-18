"""Admin review UI — side-by-side pair triage with merge/dismiss buttons.

Auth: ?key=<ADMIN_API_KEY> in the URL (solo-admin pragmatism; the page
is Disallow'd in robots.txt and never linked from public pages). The
key is carried through form posts and redirects so one paste lasts the
whole session.

Open: http://localhost:8000/admin/review?key=...&scope=intra
"""
import hmac
import os
from typing import Optional

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from backend.api.routes.review import do_dismiss, do_merge, fetch_review_page
from backend.db.client import get_client
from backend.web.routes import templates

router = APIRouter(include_in_schema=False)

SOURCE_NAMES = {1: "BuyRentKenya", 2: "Property24"}


def _check_key(key: Optional[str]):
    admin_key = os.getenv("ADMIN_API_KEY")
    if not admin_key or not key or not hmac.compare_digest(key, admin_key):
        raise HTTPException(status_code=401, detail="Invalid or missing key")


@router.get("/admin/review")
def review_page(
    request: Request,
    key: Optional[str] = Query(None),
    scope: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    applied: Optional[int] = Query(None),
    failed: Optional[int] = Query(None),
):
    _check_key(key)
    db = get_client()
    result = fetch_review_page(db, page, per_page=20, scope=scope,
                               category=category)
    total_pages = max(1, -(-(result["total"] or 0) // 20))

    return templates.TemplateResponse(request, "admin_review.html", {
        "matches": result["data"],
        "total": result["total"],
        "page": page,
        "total_pages": total_pages,
        "scope": scope or "",
        "category": category or "",
        "key": key,
        "source_names": SOURCE_NAMES,
        "applied": applied,
        "failed": failed,
    })


@router.get("/admin/stats")
def stats_page(request: Request, key: Optional[str] = Query(None)):
    """Dedup health dashboard: duplication rate per category, worst
    offenders, review queue composition."""
    _check_key(key)
    db = get_client()

    dedup = (db.table("dedup_stats").select("*")
             .order("category").order("status").execute()).data

    worst = (db.table("most_duplicated").select("*")
             .order("listing_count", desc=True).limit(15).execute()).data

    queue = []
    for scope in ("cross", "intra"):
        for tier in ("review", "auto-merge", "merged", "dismissed", "distinct"):
            n = (db.table("matches").select("id", count="exact")
                 .eq("scope", scope).eq("tier", tier)
                 .limit(1).execute()).count or 0
            if n:
                queue.append({"scope": scope, "tier": tier, "count": n})

    return templates.TemplateResponse(request, "admin_stats.html", {
        "dedup": dedup,
        "worst": worst,
        "queue": queue,
        "key": key,
    })


def _action_redirect(key: str, scope: str, category: str,
                     page: int) -> RedirectResponse:
    url = f"/admin/review?key={key}&page={page}"
    if scope:
        url += f"&scope={scope}"
    if category:
        url += f"&category={category}"
    # 303: browsers follow a POST's redirect with GET
    return RedirectResponse(url, status_code=303)


@router.post("/admin/review/{match_id}/merge")
def admin_merge(match_id: int, key: str = Form(...), scope: str = Form(""),
                category: str = Form(""), page: int = Form(1)):
    _check_key(key)
    do_merge(get_client(), match_id)
    return _action_redirect(key, scope, category, page)


@router.post("/admin/review/{match_id}/dismiss")
def admin_dismiss(match_id: int, key: str = Form(...), scope: str = Form(""),
                  category: str = Form(""), page: int = Form(1)):
    _check_key(key)
    do_dismiss(get_client(), match_id)
    return _action_redirect(key, scope, category, page)


@router.post("/admin/review/batch")
async def admin_batch(request: Request):
    """Apply a page of triage decisions in one submit.

    The form carries one radio group per pair (decision_<match_id> =
    merge | dismiss | skip). Skips are ignored; failures on individual
    pairs (e.g. already merged) don't abort the rest.
    """
    form = await request.form()
    key = form.get("key")
    _check_key(key)
    db = get_client()

    applied = 0
    failed = 0
    for name, value in form.items():
        if not name.startswith("decision_") or value == "skip":
            continue
        # Keyed by LISTING PAIR (stable across rebuilds), not match id
        # (wiped every rebuild — a nightly rebuild mid-triage used to
        # invalidate every open decision). Resolve the pair's current
        # match row at apply time.
        try:
            a_id, b_id = (int(x) for x in name[len("decision_"):].split("_"))
        except ValueError:
            continue
        try:
            row = (db.table("matches").select("id")
                   .eq("listing_a_id", a_id).eq("listing_b_id", b_id)
                   .limit(1).execute())
            if not row.data:
                failed += 1
                continue
            if value == "merge":
                do_merge(db, row.data[0]["id"])
                applied += 1
            elif value == "dismiss":
                do_dismiss(db, row.data[0]["id"])
                applied += 1
        except HTTPException:
            failed += 1

    url = (f"/admin/review?key={key}&page={form.get('page', 1)}"
           f"&applied={applied}&failed={failed}")
    if form.get("scope"):
        url += f"&scope={form.get('scope')}"
    if form.get("category"):
        url += f"&category={form.get('category')}"
    return RedirectResponse(url, status_code=303)

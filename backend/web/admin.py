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
    page: int = Query(1, ge=1),
):
    _check_key(key)
    db = get_client()
    result = fetch_review_page(db, page, per_page=20, scope=scope)
    total_pages = max(1, -(-(result["total"] or 0) // 20))

    return templates.TemplateResponse(request, "admin_review.html", {
        "matches": result["data"],
        "total": result["total"],
        "page": page,
        "total_pages": total_pages,
        "scope": scope or "",
        "key": key,
        "source_names": SOURCE_NAMES,
    })


def _action_redirect(key: str, scope: str, page: int) -> RedirectResponse:
    url = f"/admin/review?key={key}&page={page}"
    if scope:
        url += f"&scope={scope}"
    # 303: browsers follow a POST's redirect with GET
    return RedirectResponse(url, status_code=303)


@router.post("/admin/review/{match_id}/merge")
def admin_merge(match_id: int, key: str = Form(...),
                scope: str = Form(""), page: int = Form(1)):
    _check_key(key)
    do_merge(get_client(), match_id)
    return _action_redirect(key, scope, page)


@router.post("/admin/review/{match_id}/dismiss")
def admin_dismiss(match_id: int, key: str = Form(...),
                  scope: str = Form(""), page: int = Form(1)):
    _check_key(key)
    do_dismiss(get_client(), match_id)
    return _action_redirect(key, scope, page)

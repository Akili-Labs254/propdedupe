"""
PropDeDupe API: serves deduplicated property listings.

Run locally:
    uvicorn backend.api.main:app --reload --port 8000

The --reload flag watches for file changes and auto-restarts (dev only).
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from backend.api.routes.properties import router as properties_router
from backend.api.routes.review import router as review_router
from backend.api.routes.stats import router as stats_router
from backend.web.routes import router as web_router


app = FastAPI(
    title="PropDeDupe API",
    description="Deduplicated Nairobi property listings from BuyRentKenya and Property24.",
    version="0.1.0",
)

# ── CORS ─────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(properties_router)
app.include_router(review_router)
app.include_router(stats_router)

from backend.web.segments import router as segments_router
from backend.web.admin import router as admin_router
from backend.web.vanity import router as vanity_router

# Consumer HTML pages (/, /property/{slug}): server-rendered for SEO
app.include_router(web_router)
app.include_router(segments_router)
app.include_router(admin_router)
# MUST BE LAST: vanity owns "/{code}" and would shadow any single-segment
# route registered after it (/houses-for-sale, /robots.txt, /sitemap.xml).
app.include_router(vanity_router)
app.mount("/static", StaticFiles(directory="backend/web/static"), name="static")

# ── Human error pages for consumer routes ─────────────────────────
# JSON API paths keep JSON errors; everything else (consumer pages)
# renders a friendly HTML page instead of {"detail": ...}.
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.responses import JSONResponse
from backend.web.routes import templates as web_templates

_API_PREFIXES = ("/properties", "/review", "/stats", "/health",
                 "/docs", "/openapi", "/admin")


@app.exception_handler(StarletteHTTPException)
async def html_error_pages(request, exc):
    if (request.url.path.startswith(_API_PREFIXES)
            or exc.status_code not in (404, 500)):
        return JSONResponse({"detail": exc.detail},
                            status_code=exc.status_code)
    return web_templates.TemplateResponse(
        request, "error.html",
        {"status_code": exc.status_code, "detail": exc.detail},
        status_code=exc.status_code)


@app.get("/health")
def health():
    """Health check: confirms the API is running (moved from / when the
    consumer homepage took over the root URL)."""
    return {"status": "ok", "project": "PropDeDupe"}

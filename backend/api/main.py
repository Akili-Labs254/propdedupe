"""
PropDeDupe API — serves deduplicated property listings.

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

# Consumer HTML pages (/, /property/{slug}) — server-rendered for SEO
app.include_router(web_router)
app.include_router(segments_router)
app.include_router(admin_router)
app.mount("/static", StaticFiles(directory="backend/web/static"), name="static")

@app.get("/health")
def health():
    """Health check — confirms the API is running (moved from / when the
    consumer homepage took over the root URL)."""
    return {"status": "ok", "project": "PropDeDupe"}

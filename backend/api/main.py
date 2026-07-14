"""
PropDeDupe API — serves deduplicated property listings.

Run locally:
    uvicorn backend.api.main:app --reload --port 8000

The --reload flag watches for file changes and auto-restarts (dev only).
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.db.client import get_client

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


@app.get("/")
def root():
    """Health check — confirms the API is running."""
    return {"status": "ok", "project": "PropDeDupe"}


@app.get("/stats")
def get_stats():
    """Headline numbers for the dedup pipeline."""
    db = get_client()

    listings = db.table("listings").select("id", count="exact").execute()
    properties = db.table("properties").select("id", count="exact").execute()
    matches = db.table("matches").select("id", count="exact").execute()

    total_listings = listings.count
    total_properties = properties.count
    total_matches = matches.count

    dedup_rate = round((1 - total_properties / total_listings) * 100, 1) if total_listings else 0

    return {
        "total_listings": total_listings,
        "total_properties": total_properties,
        "total_matches": total_matches,
        "dedup_rate_pct": dedup_rate,
    }
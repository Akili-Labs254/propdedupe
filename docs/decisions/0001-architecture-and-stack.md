# 0001 — Architecture & stack

**Status:** accepted (Phase 0)

## Decision
A single repo with three loosely-coupled Python components — **scraper → dedup →
API** — over one PostgreSQL database (Supabase), feeding a thin frontend.

## Why
- Each component can be run and tested on its own.
- One database as the single source of truth keeps the data flow obvious.
- No microservices: unnecessary complexity for a solo project, and a time sink
  that adds no portfolio value.

## Stack
- **Scraping:** requests + BeautifulSoup. Both initial sources are server-side
  rendered, so no browser automation (Playwright) is needed.
- **Matching:** imagehash (perceptual hashing). rapidfuzz was planned for
  title similarity but never needed.
- **API:** FastAPI — typed, with auto-generated Swagger docs for free.
- **Database:** PostgreSQL via Supabase (plays to existing SQL strength).
- **Frontend:** deferred; thin by design.

## Later
The frontend ended up as server-rendered Jinja templates inside the same
FastAPI app (`backend/web/`), not a separate Next.js project. Server-side
rendering was needed for SEO anyway, and one deploy is simpler than two.

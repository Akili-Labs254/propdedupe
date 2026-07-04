# 0001 — Architecture & stack

**Status:** accepted (Phase 0)

## Decision
A single repo with three loosely-coupled Python components — **scraper → dedup →
API** — over one PostgreSQL database (Supabase), feeding a thin frontend.

## Why
- The three components are independently demoable, which maps directly to three
  interview talking points.
- One database as the single source of truth keeps the data flow obvious.
- No microservices: unnecessary complexity for a solo project, and a time sink
  that adds no portfolio value.

## Stack
- **Scraping:** requests + BeautifulSoup. Both initial sources are server-side
  rendered, so no browser automation (Playwright) is needed.
- **Matching:** rapidfuzz (fast fuzzy string matching), imagehash (perceptual
  hashing).
- **API:** FastAPI — typed, with auto-generated Swagger docs for free.
- **Database:** PostgreSQL via Supabase (plays to existing SQL strength).
- **Frontend:** deferred; thin by design.

## Interview framing
This is an **entity resolution** (record linkage) system — a well-known,
respected problem. The pipeline shape, not any single feature, is the story.

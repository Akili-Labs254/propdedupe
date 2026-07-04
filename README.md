# PropDeDupe

**A deduplication engine for the Kenyan real estate market.**

Listing sites like BuyRentKenya and Property24 Kenya are flooded with
duplicates — the same property posted by several agents, sometimes under
different neighbourhoods and prices. It makes house-hunting painful.
PropDeDupe scrapes these sites, detects which listings are the same property,
and surfaces one clean result per home.

> **Status:** In active development — Phase 0 (foundations). See the roadmap below.

## The problem

A single house can appear three or more times in search results. Scraping recon
on Property24 found one 5-bedroom house listed three times under *Westlands*,
*Loresho*, and *Mountain View* — same price, same photo. Multiply that across
thousands of listings and search becomes nearly unusable.

## How it works

A three-stage pipeline over a single PostgreSQL database:

1. **Scrape** — collect raw listings from each source.
2. **Deduplicate** — group listings that describe the same property using image
   matching, text similarity, and price/location proximity.
3. **Serve** — expose clean, deduplicated results through an API (and a thin
   frontend).

At its core this is an **entity resolution** problem: turning many noisy records
into one canonical entity per real-world property.

## Tech stack

| Layer     | Choice                                    |
|-----------|-------------------------------------------|
| Scraping  | Python · requests · BeautifulSoup         |
| Matching  | rapidfuzz · imagehash                      |
| API       | FastAPI                                   |
| Database  | PostgreSQL (Supabase)                     |
| Frontend  | Thin web UI (deferred)                    |

## Data sources

| Site             | Rendering              | Extraction strategy        |
|------------------|------------------------|----------------------------|
| Property24 Kenya | Server-side (jQuery)   | HTML selectors             |
| BuyRentKenya     | Server-side (Livewire) | JSON-LD structured data    |

Two sources, two parsing strategies, one normalized output shape — see
`backend/scraper/base.py`.

## Project structure

```
propdedupe/
├── backend/
│   ├── scraper/   # per-source scrapers + shared fetch utilities
│   ├── dedup/     # the matching engine (image, text, price/location)
│   ├── api/       # FastAPI app serving deduplicated results
│   ├── db/        # schema.sql + queries
│   └── common/    # config, shared helpers
├── frontend/      # thin UI (deferred)
├── data/          # local scratch / scraped cache (gitignored)
├── tests/
├── docs/          # architecture & decision notes
└── scripts/       # one-off utilities
```

## Getting started

```bash
# 1. Clone, then create a virtual environment
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 2. Install Phase 0 dependencies
pip install -r requirements.txt

# 3. Configure
cp .env.example .env             # then fill in your values

# 4. Smoke-test scraping against one real page
python scripts/spike_fetch.py
```

## Roadmap

- [x] **Phase 0** — repo, schema, environment, first scraper spike
- [ ] Scrape Property24 + BuyRentKenya into the database
- [ ] Deduplication engine (image → text → price/location)
- [ ] FastAPI service for deduplicated results
- [ ] Thin frontend
- [ ] Deploy

## Responsible scraping

This project scrapes only public listing data, identifies its bot honestly via
User-Agent, rate-limits requests, and caches locally to avoid unnecessary load.
Respect each site's `robots.txt` and terms of service.

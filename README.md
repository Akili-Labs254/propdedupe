# PropDeDupe

Property listing deduplication engine for the Nairobi real estate market.

## Why this exists

Nairobi's property market is split across multiple listing sites
(BuyRentKenya, Property24, among others) and they're full of duplicates.
The same house shows up 3-7 times on Property24 alone, sometimes under
different suburbs. If you're actually trying to find a house — like my
stepdad was — you end up scrolling through the same listings over and over
across multiple sites, with no way to tell what's genuinely new.

I wanted to build something that pulls listings from multiple sources,
figures out which ones are the same property, and collapses them down to
one clean result per actual house. At its core this is an entity resolution
problem — turning many noisy records into one canonical entity per
real-world property.

## What it does

Scrapes about 13,500 listings at initial scrape (July 2026) from two Nairobi property sites and
deduplicates them down to roughly 8,950 distinct properties — **about 34%
were duplicates.**

The dedup pipeline works in layers:

- **Intra-source (Property24):** groups identical listings using a composite
  key (image URL + bedrooms + baths + size + price). This catches the bulk
  of P24's re-listed bloat — developments posting the same unit type 10-50
  times, same house posted by multiple agents and agencies.

- **Cross-source (BRK ↔ P24):** matches listings across sites using
  perceptual image hashing (triple-hash with 2-of-3 agreement to handle
  P24's watermarks and different croppings across the sites), then scores
  pairs on price similarity, bedroom/bath counts, and suburb proximity via
  a hand-built adjacency map of 78 Nairobi suburbs.

Pairs are sorted into three tiers: **auto-merge** (high confidence,
verified 96% precision), **review queue** (needs a human look), and
**distinct**.

## Live API

Deployed at **https://propdedupe.onrender.com**

Try these:

- [`/stats`](https://propdedupe.onrender.com/stats) — headline dedup numbers
- [`/properties?per_page=5`](https://propdedupe.onrender.com/properties?per_page=5) — browse deduplicated properties
- [`/properties?location=karen&bedrooms=4`](https://propdedupe.onrender.com/properties?location=karen&bedrooms=4) — filtered search
- [`/review?per_page=3`](https://propdedupe.onrender.com/review?per_page=3) — review queue with side-by-side listing pairs and signal breakdowns

## Project structure

```
propdedupe/
├── backend/
│   ├── scraper/   # per-source scrapers (JSON-LD for BRK, HTML for P24)
│   ├── dedup/     # scorer, adjacency map, image matching
│   ├── api/       # FastAPI endpoints
│   ├── db/        # schema, client, save utilities
│   └── common/    # config
├── scripts/       # scraping, backfill, dedup pipeline runners
├── docs/          # pipeline plan, decision log
└── data/          # local scratch (gitignored)
```

## How to run locally

```bash
git clone https://github.com/Akili-Labs254/propdedupe.git
cd propdedupe
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# add SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, ADMIN_API_KEY to .env
uvicorn backend.api.main:app --reload --port 8000
```

## Responsible scraping

This project scrapes only publicly listed property data, identifies itself
honestly via User-Agent, rate-limits requests with random delays, and backs
off on errors. No images are rehosted — only URLs and perceptual hashes are
stored.

## What I'd do next

- Visual review page for the review queue (merge/dismiss with side-by-side images)
- Compare a learned model against the hand-tuned heuristic baseline
- Add more sources (Facebook groups, smaller listing sites)
- Consumer-facing search frontend

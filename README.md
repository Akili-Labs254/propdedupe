# PropDeDupe

A deduplication engine for Nairobi property listings. It runs
[fikia.co.ke](https://www.fikia.co.ke), a search site that shows each
property once instead of once per advert.

## Why

Nairobi listings are spread across a few portals (BuyRentKenya,
Property24 and others) and they're full of repeats. The same house gets
posted again and again, by different agents, sometimes under different
suburbs. When my stepdad was house hunting he was scrolling through the
same places over and over with no way to tell what was actually new.

So I wanted to see if I could pull listings from more than one site,
work out which adverts are the same property, and show one result per
property.

## Numbers

From the last full crawl (6 September 2026):

- **57,744** live adverts become **35,791** distinct results
- **718** properties were found on both portals
- 151 tests

Not all of that gap is "duplicates". Some of it is the same advert posted
many times, and some is a development advertising every identical unit
separately, which shows as one result on purpose. ADR 0009 goes into it.

## How it works

1. **Scrape.** One scraper per site (Property24 is HTML, BuyRentKenya was
   JSON-LD). Both produce the same `ScrapedListing` shape, so nothing
   downstream cares where a listing came from. Upserts are keyed on
   `(source, source_listing_id)`.
2. **Hash images.** Three perceptual hashes per cover photo (phash, ahash,
   whash). Watermarks throw individual hashes off in different ways, so a
   pair needs 2 of the 3 to agree.
3. **Match within a site.** Exact re-posts share a `cluster_key` (image URL
   + beds + baths + size + price). Re-uploaded photos are caught by hash
   distance.
4. **Match across sites.** Hash blocking cuts millions of possible pairs
   down to the ones worth scoring, then each pair is scored on image,
   price, beds, baths and location. Location uses a hand-built map of
   Nairobi suburbs and which ones border each other, because the same
   house often gets listed under a neighbouring suburb.
5. **Tiers.** Every pair ends up as auto-merge, review or distinct. No
   single field can veto a match, but a far-apart location or a different
   bed count blocks auto-merge. A hand check in July found about 96% of
   auto-merges were correct.
6. **Group.** Union-find turns matched pairs into properties. The
   properties table is rebuilt every run, so URLs (slugs) are tied to
   listings rather than property ids, and old slugs 301 to new ones.
7. **Serve.** FastAPI with server-rendered Jinja pages. Suburb and bedroom
   pages are generated from the data for search engines. Every result
   links out to the original advert. Images are never rehosted.

## Things that went wrong

Most of what I learnt came from these:

- **BuyRentKenya blocked the scraper** on 2 September after a re-platform.
  Getting around it would mean faking browser fingerprints, and I decided
  not to. Those listings are frozen as of that date (ADR 0010).
- **A source marked as stale when it wasn't scraped.** When CI only
  scraped one site, the stale check ran on both and marked all 42k
  Property24 listings stale. Fixed by skipping any source that wasn't
  crawled in that run.
- **Supabase's 60 second gateway limit.** Big single UPDATE/DELETE requests
  just timed out, and retrying made it worse (lock conflicts). Every bulk
  write is now done in batches of 1,000 rows.
- **Silent parser breakage.** Property24 changed its markup and prices came
  back NULL for eleven days without anything failing. I only noticed
  because a page looked empty.

## Current state

- Both portals block datacenter IPs, so scraping runs by hand from a home
  connection rather than on a schedule. See `docs/runbook.md`.
- The rebuild clears tables before refilling them, so the site is empty
  while it runs. Building into new tables and swapping is next.
- A check on per-field fill rates after each scrape would have caught the
  NULL prices. Also not built yet.

## Stack

Python 3.9, requests + BeautifulSoup, imagehash, FastAPI, Jinja2,
PostgreSQL on Supabase, hosted on Render.

```
backend/
  scraper/   one module per site
  dedup/     scorer, suburb adjacency map, slugs, development grouping
  db/        schema + SQL migrations, Supabase client, upserts
  web/       site routes, SEO pages, admin review queue
  api/       FastAPI app + JSON endpoints
scripts/     refresh pipeline, dedup rebuild, backfills
tests/
docs/
  decisions/ architecture decision records (ADRs)
  runbook.md how to run it and what has broken before
```

## Running it locally

```bash
git clone https://github.com/Akili-Labs254/propdedupe.git
cd propdedupe
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in your own Supabase project's values
uvicorn backend.api.main:app --reload --port 8000
python -m pytest
```

There's also a JSON API: `/properties` (with filters) and Swagger docs at
`/docs`.

## Scraping politely

Only publicly listed data. The scraper identifies itself with a real
contact address, waits 5 seconds between requests, backs off on errors,
and stores image URLs and hashes rather than copies of the images.

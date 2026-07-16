# PropDeDupe Runbook

Operational reference: how to run everything, and what the failures
we've actually hit mean. Update this when something new bites.

## Regular operations

```bash
# Full refresh cycle (scrape → hashes → stale → dedup). Hours.
caffeinate -i python -u -m scripts.refresh --all 2>&1 | tee data/refresh.log

# Read-only preflight / post-run sanity
python -m scripts.refresh --check

# Smoke-test parsers after site changes (never runs stale pass)
python -m scripts.refresh --scrape --max-pages 3

# Dedup rebuild only (re-runnable, clears + rebuilds matches/properties)
caffeinate -i python -u -m scripts.persist_dedup 2>&1 | tee data/persist.log

# Resume a rebuild that died during singletons
python -m scripts.persist_dedup --singletons-only
# ...then clean orphaned properties in SQL Editor:
# delete from properties p where not exists
#   (select 1 from listings l where l.property_id = p.id);

# Intra-source candidate histogram (read-only)
python -m scripts.intra_source_match

# Web app
uvicorn backend.api.main:app --reload --port 8000
# Admin queue: /admin/review?key=<ADMIN_API_KEY>&scope=intra
# Swagger:     /docs        Tests: pytest tests/test_web.py

# One-off backfills (re-runnable)
python -m scripts.backfill_suburbs
```

## SQL files (backend/db/) — run once each in Supabase SQL Editor
| file | provides |
|------|----------|
| `refresh_support.sql` | status backfill/default/check, price_history trigger + baseline |
| `slug_support.sql` | slug column, property_slugs redirects, link_listings RPC, grants |
| `price_range.sql` | price_min/max + backfill |
| `property_first_seen.sql` | crawl stamp + backfill |
| `review_decisions.sql` | durable merge/dismiss decisions |
| `matches_scope.sql` | cross/intra scope on matches |
| `seo_support.sql` | suburb column, suburb_stats view |
| `hash_invalidation.sql` | null hashes when a cover photo URL changes |
| `rental_support.sql` | category columns (sale/rent partition), per-category suburb_stats |
| `admin_stats.sql` | dedup_stats + most_duplicated views, hide_generic_images() |

All idempotent. VACUUM statements must run as single statements (the
editor wraps multi-statement queries in a transaction).

## Failure modes seen in the wild

**`statement timeout` (57014)** — bulk UPDATE/DELETE too slow, usually
after table bloat from repeated full-table rewrites. Fixed globally:
`alter role service_role set statement_timeout = '10min'`. If rebuilds
slow down again: `vacuum (analyze) listings;` (single statement).

**`ConnectionTerminated / GOAWAY`** — Supabase's proxy closes an HTTP/2
connection after ~10k requests. Handled: `_with_retry` reconnects, and
the link_listings RPC cut per-run request count ~100×. If seen
elsewhere, wrap the call in `_with_retry`.

**`permission denied for table X` (42501)** — new table missing grants.
`grant select, insert, update, delete on public.X to service_role;`
Every new table/view needs this in its SQL file.

**Admin key 401 after editing `.env`** — dotenv loads at process start;
`--reload` watches .py files only. Restart uvicorn. Also URL-encode the
key (`urllib.parse.quote`) if it has special characters.

**Silent rebuild failure** — a crashed persist run leaves the site
serving stale or zero properties with no obvious symptom. Always check
the log tail + `--check` after a run. (The scheduler, when built, must
alert on non-zero exit — a run died silently once already.)

**Scrape looks broken (0 listings/page)** — site markup changed. Parser
fix required; the stale-pass guard (<50% of active count) protects the
DB meanwhile.

**Pagination never ends (404s to the page cap)** — sites differ in
end-of-listing behaviour: P24 serves empty pages, BRK 404s. Both
scrapers stop on 2 empty pages OR 3 consecutive failures.

**`chk_status` / check-constraint violation on a rarely-written value**
— a legacy constraint predating the pipeline didn't allow 'delisted';
it only fired on the first run old enough to delist anything.
Constraints on legacy columns fire on FIRST WRITE of a new value, not
at deploy time — test rare transitions early.

**Resuming a crashed run's --stale stage** — NEVER bare `--stale`: the
cutoff defaults to the new invocation's start time and mass-stales
everything scraped before it. Pass the original run's timestamp:
`--stale-cutoff "<'Run started' line from the crashed log>"`.

## Invariants (do not violate casually)
- Python + SQL cluster_key formulas byte-identical.
- `.order("id")` before `.range()` on every paginated fetch.
- Anything that must survive a rebuild is keyed to listings (ADR 0003).
- New properties columns must be written by ALL THREE persist steps
  (cross-source, clusters, singletons) plus a backfill for the current
  build.
- price NULL for POA, never 0.
- Intra auto-merge cut (0.10) changes only with a new audit (ADR 0005).

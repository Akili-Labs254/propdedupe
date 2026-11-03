# PropDeDupe Runbook

Operational reference: how to run everything, and what the failures
we've actually hit mean. Update this when something new bites.

## Refreshing data (manual)

Both sources block datacenter IPs (Property24 since July, BuyRentKenya
since 2 Sep, see ADR 0010), so nothing scrapes from CI. The schedules in
`.github/workflows/refresh.yml` are commented out and scraping runs from a
laptop on a home connection.

1. Scrape, hash and stale-check. Overnight; keep the lid open.

   ```bash
   set -o pipefail
   LOG=logs/refresh-$(date +%Y%m%d-%H%M).log
   caffeinate -is python -u -m scripts.refresh --scrape --hashes --stale \
       --sources p24 --rent-max-pages 1500 2>&1 | tee "$LOG"
   echo "exit=$? finished $(date)" | tee -a "$LOG"
   ```

   `-is`, not `-i`: `-s` also blocks system sleep while on mains power.
   A dropped connection kills the hash stage (happened on 30 Sep).
   Deliberately not `--all`, so the site doesn't change while nobody is
   watching.

2. Check the log before going further:

   ```bash
   grep -E "exit=|SKIPPING|complete=|403|Forbidden|→ stale" logs/refresh-*.log | tail -30
   ```

   Also check the price fill rate for listings seen today. P24 prices
   once went NULL for eleven days without anything failing.

3. Only if that looks sane, rebuild (watch it; property pages 404 for
   the ~90 minutes it runs, see "Known gaps" below):

   ```bash
   python -u -m scripts.persist_dedup 2>&1 | tee logs/rebuild-$(date +%Y%m%d-%H%M).log
   ```

If a run dies after scraping, resume with `--hashes`, then
`--stale --stale-cutoff "<'Run started' line from that log>"`. Never a
bare `--stale` (see below).

## Regular operations

```bash
# Full cycle in one go (scrape → hashes → stale → dedup). Hours.
caffeinate -is python -u -m scripts.refresh --all --sources p24 2>&1 | tee logs/refresh.log

# Read-only preflight / post-run sanity
python -m scripts.refresh --check

# Smoke-test parsers after site changes (never runs stale pass)
python -m scripts.refresh --scrape --max-pages 3

# Dedup rebuild only (re-runnable, clears + rebuilds matches/properties)
caffeinate -is python -u -m scripts.persist_dedup 2>&1 | tee logs/persist.log

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

## SQL files (backend/db/): run once each in Supabase SQL Editor
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

**`statement timeout` (57014)**: bulk UPDATE/DELETE too slow, usually
after table bloat from repeated full-table rewrites. Fixed globally:
`alter role service_role set statement_timeout = '10min'`. If rebuilds
slow down again: `vacuum (analyze) listings;` (single statement).

**`ConnectionTerminated / GOAWAY`**: Supabase's proxy closes an HTTP/2
connection after ~10k requests. Handled: `_with_retry` reconnects, and
the link_listings RPC cut per-run request count ~100×. If seen
elsewhere, wrap the call in `_with_retry`.

**`permission denied for table X` (42501)**: new table missing grants.
`grant select, insert, update, delete on public.X to service_role;`
Every new table/view needs this in its SQL file.

**Admin key 401 after editing `.env`**: dotenv loads at process start;
`--reload` watches .py files only. Restart uvicorn. Also URL-encode the
key (`urllib.parse.quote`) if it has special characters.

**Silent rebuild failure**: a crashed persist run leaves the site
serving stale or zero properties with no obvious symptom. Always check
the log tail + `--check` after a run.

**Scrape looks broken (0 listings/page)**: site markup changed. Parser
fix required; the stale-pass guard (<50% of active count) protects the
DB meanwhile.

**Pagination never ends (404s to the page cap)**: sites differ in
end-of-listing behaviour: P24 serves empty pages, BRK 404s. Both
scrapers stop on 2 empty pages OR 3 consecutive failures.

**`chk_status` / check-constraint violation on a rarely-written value**:
a legacy constraint predating the pipeline didn't allow 'delisted';
it only fired on the first run old enough to delist anything.
Constraints on legacy columns fire on FIRST WRITE of a new value, not
at deploy time: test rare transitions early.

**`.env` silently overriding a fix in `config.py`**: the User-Agent
contact and the request delay were corrected in `config.py` on 2 Sep,
but a local `.env` copied from an old `.env.example` still set both, and
`os.getenv` prefers the environment. Local scrapes kept sending a
placeholder contact at a 2s delay until November. When changing a
default, check `.env` too, or print `config.USER_AGENT` before a run.

**Resuming a crashed run's --stale stage**: NEVER bare `--stale`: the
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

## Known gaps
- `persist_dedup` clears before it rebuilds, so the site serves nothing
  while it runs and stays empty if it crashes. Build-then-swap would fix
  this; not done yet.
- No per-field fill-rate check after a scrape. A parser that half-breaks
  (prices NULL, location swallowing headline text) currently only shows
  up when someone looks at the site.
- ADR 0009 (collapse development listings) is decided but not built.

# 0004b: Refresh pipeline and stale detection

**Status:** accepted (product track, July 2026)

## Refresh orchestration (`scripts/refresh.py`)
Stages: scrape → incremental hash backfill → stale detection → dedup
rebuild. Each runnable alone. Scrapers paginate dynamically (stop after
2 empty pages): hardcoded page counts drifted within 3 weeks.

## Stale detection rules
- Upsert on `(source_id, source_listing_id)` bumps `last_seen` and
  resets `status='active'` (re-seen listings reactivate).
- Active listing not seen this run → `stale`.
- Stale for `DELIST_DAYS` (7) by `last_seen` → `delisted`.
- **Guard**: a source whose scrape saved < 50% of its previous active
  count skips its stale pass: a broken parser must not mass-stale a
  source. `--max-pages` smoke tests also skip it.
- Consumer pages serve `properties.status='active'` only (active = any
  member listing active).

> **Superseded in part by ADR 0008 (Aug 2026).** Stale detection now runs
> per (source, category) and requires the crawl to have reached the end of
> pagination; the `--max-pages` special case is gone. The churn figure
> below was measured under the bug 0008 fixes and is not reliable.

## Price history
Captured by a Postgres trigger on `listings` (insert, or update where
price changed): trigger rather than app code so *any* writer records
history. Baseline seeded once so the first change has a "from" value.
Measured churn, first re-scrape after 3 weeks: ~8% of listings gone.

## Durable review decisions
See ADR 0003: decisions are keyed to listing pairs and reapplied every
rebuild, for the same reason slugs are: listings are the only stable
identity in the system.

# 0008 — Staleness requires a completed crawl

**Status:** accepted (product track, August 2026)
**Amends:** 0004 (stale detection rules)

## Problem

On 2026-08-12 a P24 refresh with `--rent-max-pages 1500` reactivated
7,240 listings that a previous run had marked stale or delisted. They had
never been removed from Property24. They were sitting past the page cap
of the earlier run, were therefore never fetched, and the stale pass read
"not seen this run" as "gone from the site".

At the time, 17,818 listings were flagged stale or delisted. **41% of
that set was wrong**, and wrong in the flattering direction — it inflated
how many adverts we appeared to be cleaning up.

Two design faults combined:

1. **The stale pass ran per source, not per (source, category).** Sale
   was crawled to the end; rent was capped. One decision was applied to
   both.
2. **The guard checked the wrong thing.** `main()` skipped the stale pass
   when `--max-pages` was set, but not when `--rent-max-pages` was. It
   inspected CLI arguments rather than what the scrape actually did.

The pre-existing `MIN_SCRAPE_RATIO` guard did not catch it: the capped
rent scrape saved 31,500 listings against ~22,000 active, far above the
50% floor. It was a large, healthy-looking, incomplete scrape.

## Decision

**A listing's absence is evidence of removal only if the crawl reached
the end of the list.**

- Both scrapers' `run()` returns `(saved, complete)`. `complete` is True
  only when pagination terminated on its own — an empty-page streak, or
  the 404 streak that both sites serve past their last page. Exhausting
  the page cap is **not** complete.
- `stage_scrape` records `complete` per category, along with that
  category's own prior active count.
- `stage_stale` runs per (source, category) and skips any category whose
  crawl did not complete.
- `_flip_status_chunked` is scoped by category, so the two can diverge.
- The blanket `--max-pages` skip in `main()` is removed. The scraper's
  own report supersedes it, covers both caps, and cannot drift out of
  sync with the CLI.

`--force` overrides the completeness and ratio guards. It does **not**
override the "source not scraped this run" guard: no cutoff makes staling
an unscraped source correct.

## Why a 404 streak counts as complete

Both sites 404 past the last page rather than serving an empty one, so
that streak is the normal terminal condition, not an anomaly. Treating it
as incomplete would make BRK unstaleable forever.

A mid-run IP block also produces a 404 streak and would wrongly look
complete. `MIN_SCRAPE_RATIO` is the second line of defence for that case,
and is why it stays.

## Consequences

- A capped category never stales. Dead rentals accumulate until a crawl
  runs to completion. That is the correct trade: a stale listing shown is
  a worse-quality result, a live listing hidden is lost inventory, and
  the false-positive rate was 41%.
- **The "~8% of listings gone" churn figure in ADR 0004 was measured
  under this bug and is not reliable.** Re-measure after a completed
  crawl before it is quoted anywhere public.
- "We remove expired adverts" is only defensible for categories that
  complete. Sale does; rent does not yet.
- `refresh.py` now has tests (`tests/test_refresh.py`). It had none —
  the only code in the project that mass-mutates status across tens of
  thousands of rows was the only significant module unguarded.

## Alternatives rejected

**Count consecutive misses before demoting.** Robust to transient
failures, but not to a permanent cap: a listing living past page 1,500
is missed every run, so it still gets staled, only later. Solves a
different problem.

**Infer which listings the crawl covered.** Not possible — P24 reorders
results between requests, so page number does not map to any stable set.

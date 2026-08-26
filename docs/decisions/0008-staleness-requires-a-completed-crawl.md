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

### Amended 2026-08-26 — only 4xx counts

The original version treated **any** exception streak as the end of the
list, and noted that a mid-run block would wrongly look complete, with
`MIN_SCRAPE_RATIO` as the second line of defence.

That happened within two weeks. On 26 Aug P24 returned 503s after 58 of
522 sale pages; rent 503'd on page one. Both reported `complete=True`,
one having saved **zero** listings. The ratio guard caught both and
skipped their stale passes — it worked exactly as designed — but the
completeness flag itself was wrong, and the whole design rests on it.

So `is_end_of_pagination` (backend/scraper/base.py) now inspects the
status:

| outcome | complete |
|---|---|
| 404 / 410 | **True** — we walked off the end |
| 5xx | False — the server is unwell, we learned nothing |
| timeout / connection error | False |
| any exception with no HTTP status | False — fail closed |

A streak is only "the end" if **every** failure in it was 4xx; one 5xx
poisons it. A successful page resets the verdict, so a transient blip
early on can't taint a genuine ending later.

`MIN_SCRAPE_RATIO` stays. Two independent guards is the point — the
26 Aug incident is precisely what happens when you rely on one.

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

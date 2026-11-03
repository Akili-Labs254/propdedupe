# 0007: Manual overrides may relax heuristics, never invariants

**Status:** accepted (Phase 1, July 2026)

## Problem
A live property page showed "KSh 165,000 – 33,000,000, 3–4 bed": a
Westlands rental (KSh 165k/month) welded to a Kiambu Road house for sale
(KSh 33m). The two listings agreed on nothing: different category,
suburb, beds, baths, price, and phashes **36 bits apart** against a
blocking threshold of ≤4.

No auto-merge produced it. `matches` held no auto row for the rental at
all. The union came from a single durable row:

```
review_decisions: (19524, 67062, 'merge', 2026-07-19 11:28:32)
```

Recorded during a rapid batch-triage session (~1 pair/second), then
faithfully reapplied on every rebuild for a week.

## Root cause
Review decisions override the scorer in two places. Only one is safe.

1. **Candidate pairs** (`persist_dedup` ~L494/L587): the decision flips
   an existing pair's tier to `merged`/`dismissed`. Safe: the pair came
   through blocking, and blocking loops *per category*, so the pair is
   same-category by construction. The human overrides the score, not the
   partition.
2. **Non-candidate pairs** (~L643): merge decisions are unioned even
   when blocking didn't surface them this run. This branch is necessary:
   `matches` is wiped every rebuild, and a pair merged last week may not
   re-block this week (invalidated hashes, staled listing, windowed rent
   scrape), so without it manual work would silently stop applying.

Branch 2 reads two listing IDs from a table and unions them having never
passed through blocking or scoring. It was the only place in the pipeline
joining two listings with **zero similarity evidence**.

The location and beds gates did not fire because gates live inside the
scorer, and the scorer never ran on this pair. A gate cannot veto what it
has not seen.

## Decision
**An override may relax a heuristic. It may not cross an invariant.**

- The location gate (≥0.5 → review) and beds gate (confirmed differing
  beds → review) are *heuristics*: they encode "probably not the same
  house." Sources disagree on beds constantly (DSQ counted or not), so a
  human must be able to overrule them. That is what review is for.
- `category` (sale|rent) is not a heuristic. It is a definition, and a
  hard partition in all matching (standing rule). A flat rented monthly
  and a house sold outright are not one property regardless of human
  confidence.

Enforced at two layers:

- `persist_dedup.py` skips cross-category merge decisions in the
  non-candidate branch and warns with the offending row's IDs.
- `backend/db/review_guard.sql`: a `before insert or update` trigger on
  `review_decisions` raising on cross-category merges, so the row cannot
  be recorded at all. Defence at the write site, not just the read site.

## Blast radius
1 bad property out of 31,155; 1 bad decision out of 116 merges. The
offending row was deleted; the next full rebuild cleared the page. The
narrowness is why it survived a week: the merge reapplied *identically*
every run, so it read as stable rather than corrupt.

## Residual
The guard covers cross-category only. Same-category merges that are
merely wrong (different suburb, >5× price gap) remain possible and are a
judgement call, not an invariant breach: three such pairs from the same
July 19 session are flagged for manual re-review. If that class recurs,
the answer is a confirmation step on high-divergence pairs in the batch
UI, not a hard block: the decision rate (~1/second) is the real variable.

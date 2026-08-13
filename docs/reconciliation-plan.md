# Reconciliation plan

For placement applications, September 2026.

The task "walk every file until I can explain any function cold" has no
start, no unit of progress and no finish line, which is why it keeps
losing to building. This gives it all three.

**The reframe:** nobody asks you to explain line 340 of
`persist_dedup.py`. They ask "tell me about a hard bug", "what would you
do differently", "why did you build it that way". So the deliverable is
**eight written stories**, not forty read files. Writing them is what
makes the files navigable under pressure — the recall follows the
narrative, not the other way round.

One sitting each. Write in your own words, in a scratch file, out loud if
it helps. Tick them off.

---

## The boundary

**4–14 July — CV track.** Scrapers, `cluster_key`, perceptual hashes,
triple-hash blocking, the weighted scorer, adjacency map, persist
pipeline, RLS, FastAPI, Render. Largely reconciled already.

**15 July — the pivot.** `Pre-publish cleanup` closes the CV track;
`Add stable slugs, consumer frontend, durable review decisions` opens the
product track, the same day.

**15 July – 12 August — product track.** Four weeks, ~50 commits, five
ADRs. This is the unreconciled part and where the plan is weighted.

---

## Session 1 — the stale-detection bug (start here)

**Why first:** it's the strongest story in the project and you lived it
today, so it costs the least to write.

Read: `docs/decisions/0008-staleness-requires-a-completed-crawl.md`,
`scripts/refresh.py::stage_stale`, `tests/test_refresh.py`.

The story: a refresh reactivated 7,240 listings. "Not seen this run" was
being read as "removed from the site", but the crawl had stopped at a
page cap and never checked them — 41% of everything marked gone was
wrong, and wrong in the flattering direction. Fix: the scraper reports
whether it reached the end of pagination; stale detection moved to
per-category. Then the tests the file had never had.

Interviewer will ask: *how did you know the fix worked?* (Complete crawl
produced 11 and 25 stale, against 1,123 and 1,602 from a capped one.)
And: *what would have caught it earlier?* (A test. There were none.)

## Session 2 — stable identity

Read: ADR 0003, `backend/dedup/slugs.py`, the `slug_snapshot` guard in
`scripts/persist_dedup.py`.

Why slugs key to **listings**, not properties: the dedup rebuild
regenerates properties every run, so property IDs are not stable — but
listings are. Covers `property_slugs`, the 301 chains, and the
`SNAPSHOT_SHRINK_GUARD` (set to 0.7 because 0.5 wouldn't have caught the
real incident at 27,968/54,536 = 0.513).

Ask: *what breaks if you skip this?* Every SEO URL 404s after a rebuild.

## Session 3 — dedup as weighted evidence

Read: ADR 0004 (policy) and 0005 (measured threshold),
`backend/dedup/scorer.py`, `backend/dedup/adjacency.py`.

Five signals (image .30, price .25, beds .20, baths .05, location .20),
the location gate, the beds gate, three tiers. The rule that matters:
**no single field may veto a match** — sequential filters were rejected
deliberately. Image match is not conclusive on its own because
developments reuse renders. Location is a score, never a blocking key,
because the same house appears under three suburb names.

Ask: *why not just match on image?* You have a live counter-example —
96 phash groups holding 4,183 listings.

## Session 4 — category as a hard partition

Read: ADR 0006, `backend/db/save.py` (the category-conflict guard),
ADR 0007 (`review_decisions` may not cross invariants).

Rentals arriving as a first-class category, why a listing's category can
never be overwritten, and the July 19 incident: a Westlands rental welded
to a Kiambu Road house by one durable review decision, faithfully
reapplied on every rebuild for a week.

Ask: *how do you stop a human override corrupting the data model?*

## Session 5 — surviving the free tier

Read: the 22–24 July commits, `backend/db/client.py`,
`_flip_status_chunked`, `_with_retry`.

The 60-second API gateway limit and why every bulk write is chunked. The
600s PostgREST timeout for the batch pipeline and the lock-collision it
prevents. The `lru_cache` on `get_client` — three OOM crashes on 7 August,
roughly seven hours apart, because every web request built a client with
its own connection pool and nothing closed them.

Ask: *what does this code do differently because it's on a free tier?*
This is a genuinely good answer — most candidates have never had a
binding resource constraint.

## Session 6 — SEO surface

Read: 26–30 July commits, `backend/web/segments.py`, `MIN_SEGMENT`.

Suburb slugification and the 301 for the legacy space form; percentile
price bands instead of raw min/max; the sitemap cut from ~28,870 to ~810
URLs; thin pages served `noindex` rather than 404 (978 of them).

Ask: *why not submit every URL you have?* Thin-content penalties, and a
sitemap is a quality signal not a dump.

## Session 7 — measurement

Read: `backend/web/vanity.py`, the outbound-click listener in
`base.html`, `gtm/google-ads-test-prep.md`.

TikTok strips referrers and viewers type URLs, so the **path itself
carries the attribution**. 302 not 301, because a cached redirect strands
viewers of an old video forever. Then the honest part: the vanity codes
recorded zero visits, and the funnel arithmetic says why — ~0.075% of
views convert, so 100 visitors/week needs 133,000 views.

Ask: *how do you know your marketing worked?* Most people can't answer.
You can, including when the answer was "it didn't."

## Session 8 — what you'd do differently

Not a file — a page of judgement. Candidates:

- No tests on `refresh.py` until it had already cost 7,240 listings
- `source_count` counts dead listings, so the sitemap's multi-source
  filter selects on an inflated signal
- `hide_generic_images` treated a dedup miss as an image problem
- Cross-source dedup — the hardest thing built — resolves ~1% of
  properties, because coverage never grew to match it
- Whether a 64-unit development is 64 properties or one price range,
  still undecided

Ask: *what's the weakest part of this?* Having a real answer ready is
worth more than any of the above going well.

---

## Practical drills

`docs/code-drills.md` — the other half of the job. The eight sessions
above prepare "tell me about a hard bug"; the drills prepare "walk me
through this function", "what happens if I pass it X", "what breaks if I
delete this line", and the hostile questions.

Swap a drill set in for a writing session whenever you want something
practical rather than reflective. Drill E (hostile questions) is worth
doing early — it tells you which stories need the most work.

## Finishing

Eight sittings. When they're written, do one pass opening each file named
above and checking the story still matches the code — that's the "explain
it cold" part, and it takes an hour once the narratives exist.

Then stop. The point is not to memorise 40 files; it's to be unsurprised
by any of them.

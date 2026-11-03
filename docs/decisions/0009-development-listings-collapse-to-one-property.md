# 0009 — Development listings collapse to one property

**Status:** accepted (product track, August 2026)
**Extends:** 0004 (intra-source dedup policy), 0005 (measured threshold)

## Problem

Sorting Westlands 2-beds by price surfaced it: 21 of the first 24 results
were the same property. Measured across the whole active sale corpus:

| measure | value |
|---|---|
| active sale properties | 15,577 |
| sharing title + price + suburb + beds | 12,527 (80.4%) |
| distinct rows if collapsed | 3,930 |
| largest single group | 807 |

A results page is the product. If four fifths of it is one advert repeated,
the deduplication engine is not delivering its own promise — it removes
duplicates *across* sources and misses the far larger repetition *within*
one.

The existing pipeline could not catch these. Both stages are image-driven:
step 1 blocks on perceptual hashes, step 2 on `cluster_key`, which includes
`image_url`. These listings have **different images**, so they fail both.

## Why the images differ, and why that kills the obvious safeguard

The first proposed rule was "same suburb + beds + price, with image
similarity as a safeguard against merging two genuinely different
buildings." **The safeguard does not exist.** Within (suburb, beds, price)
groups:

- mean distinct images per group: **3.38**, max **30**
- groups sharing a single image: **4 of 858**

The reason is structural. Cross-source duplication is *the same advert
reposted*, so the image is identical and hashing works. A development
advertised unit by unit is *different photographs of the same building* —
one advert leads on the kitchen, the next on the balcony. Image similarity
is evidence of a shared advert, not of a shared building, and this is the
case that separates the two.

Inspecting the largest Westlands group makes the point sharper still. All
234 properties are byte-identical on every stored field:

```
location: "Westlands Area"
title:    "2 Bed Apartment with En Suite in Westlands Area"
bathrooms: NULL     size_sqm: NULL
```

There is no field on which these could be told apart. The data does not
merely fail to distinguish them — it contains nothing that could.

## Decision

**Four or more adverts identical on every human-readable field describe one
property and are collapsed into one.**

The development key is:

```
category | suburb | location | title | bedrooms | bathrooms | size_sqm | price
```

with a sentinel for NULL `bathrooms` and `size_sqm` so that absent values
group together rather than each becoming its own row. Image is **not** in
the key, and that is the whole point of this ADR.

### Why every field, rather than suburb + beds + price

The loose rule was measured against the strict one across all active
properties:

| rule | groups | absorbed | rows removed | biggest |
|---|---|---|---|---|
| A — suburb + beds + price | 2,577 | 23,273 | 20,696 | 240 |
| B — + title, location, baths, size | 3,430 | 16,722 | **13,292** | 239 |

The largest group moves 240 → 239. The floods are byte-identical anyway,
so the strict rule catches effectively all of the problem while **declining
7,404 merges** the loose rule would have made — precisely the cases where
price and bed count coincided but the advert text did not, which is where
two different buildings would hide. A round number like 7,000,000 recurs
across Westlands blocks; text does not.

The safety costs almost nothing. Take it.

### Why a minimum group size of 4

Matching on every field still permits a false merge: titles are generic
("2 Bed Apartment with En Suite in Kilimani"), `bathrooms` and `size_sqm`
are frequently NULL, and prices cluster on round numbers. Two genuinely
different Kilimani flats can collide on all eight fields.

Group size is itself evidence, and the risk is not spread evenly:

| group size | groups | rows removed | share of collapse |
|---|---|---|---|
| 2 | 1,718 | 1,718 | 12.9% |
| 3 | 681 | 1,362 | 10.2% |
| 4–5 | 488 | 1,634 | 12.3% |
| 6–10 | 338 | 2,140 | 16.1% |
| 11–25 | 155 | 2,340 | 17.6% |
| 26+ | **50** | 4,098 | **30.8%** |

Fifty groups produce 31% of the entire collapse. At that size the
development reading is the only plausible one — nobody owns 234 independent
flats that coincide on eight fields. At size 2 the reading is genuinely
ambiguous, **and ambiguous in both directions**: a pair may be two
different flats, or one flat photographed by two agents, which is a true
duplicate that *should* merge. Nothing in the data separates those, so the
rule declines to guess.

Pairs and triples are therefore left alone. That forgoes 23% of the
possible collapse and removes most of the false-merge exposure — the same
trade the scorer makes at its review tier, and the same principle: merge
the unambiguous, concede the marginal, never let one signal decide alone.

`MIN_DEVELOPMENT_GROUP = 4` is a plausibility judgment, not a measurement.
`scripts/audit_development_groups.py` samples groups per size band into a
worksheet so it can be checked against the source adverts, as the scorer's
96% was. Revise this ADR with the measured false rate.

### The property page must show every member advert's image

Whatever the threshold, the card and hero image come from one
representative, so a merge hides the other adverts' photographs from the
results page. That, not the lost row, is the real cost of a wrong merge:
the buyer never sees the second flat's interior.

Rendering all member images on the property page reduces a false merge to
"two similar flats presented on one page, both linking out" — which for
adverts that were indistinguishable in the results list is arguably better
than two identical-looking rows. The page already lists every member advert
with its own source link, price and stale marker; only the images are
missing. **This ships with the merge, not after it.**

### Why not display-level grouping with a unit count

The first design was to keep the rows and render "21 units available".
Rejected on two grounds.

**It shows information the buyer cannot use.** In this market unit 2A
versus 2B carries nothing a buyer can act on; bed count and price are the
decision. Quantity is worth knowing, but it is properly learned when an
agent states it — through a claimed listing, or a developer posting
directly — not inferred by us.

**The count would not be true.** 21 adverts may be 21 units, or one unit
posted 21 times by an agent working the portal's ordering. Nothing in the
data separates those. "21 units available" asserts a fact the corpus
cannot support, which is the same class of overclaim ADR 0008 was written
to stop.

## Consequences

| | before | merge all | **merge 4+** |
|---|---|---|---|
| active properties | 35,088 | 21,796 | **~24,876** |
| rows removed | — | 13,292 (38%) | **10,212 (29%)** |

The figures below assume the threshold of 4.

- **The public headline changes and must be reworded, not just renumbered.**
  54,269 active adverts now reduce to roughly 24,900 results — against the
  37% quoted today. But it would be wrong to call all of that
  "duplicates removed": a 234-unit development collapsing to one row is not
  233 repeated adverts. The honest form is **"54,000 adverts become 21,800
  distinct results"**, with same-advert duplication quoted separately where
  a specific number is needed. Fix this on the homepage and anywhere else
  the figure is quoted, at the same time as the rebuild — it is currently understated, which
  is the safe direction, but it is wrong either way.
- **Reversible.** This is a grouping rule re-derived from listings on every
  rebuild, not a durable human decision of the kind ADR 0007 protects.
  Changing the key changes the output on the next run; nothing is lost.
- **The review queue is unaffected.** Those pairs are cross-source
  candidates scored 0.36–0.50 — a different axis. This rule neither clears
  nor grows the queue.
- **Multi-source properties are barely touched:** only 180 groups contain a
  property with `source_count > 1`, and merging can only raise
  `source_count`, so `SITEMAP_MULTI_SOURCE_ONLY` admits more pages, never
  fewer.
- **Slugs churn once.** ~10,200 properties disappear and their slugs enter
  the 301 chains via `property_slugs`. This is the largest single movement
  since slugs were stabilised; `SNAPSHOT_SHRINK_GUARD` at 0.7 will trip on
  a 29% shrink and must be overridden deliberately for this run rather than
  lowered permanently.
- **The key casefolds and collapses whitespace**, which the SQL used for
  the measurements above did not. Actual collapse will be slightly larger
  than the figures quoted here. Re-measure from the script, not from this
  table, before quoting anything publicly.

## Re-measured 2026-09-02, after the BRK re-platform

BuyRentKenya moved to a new stack on 1 September and removed a large
amount of inventory in the process — including all 551 "1 Bed Apartment
with En Suite in Kilimani" adverts that were the worst example in this
document. They are now delisted or stale, correctly caught by the stale
pass. The problem partly solved itself.

| | 28 Aug | 2 Sep |
|---|---|---|
| active properties | 35,088 | 34,142 |
| rows removed at threshold 4 | 10,212 | **8,835** |
| groups at threshold 4 | — | 848 |
| biggest group | 239 | **114** |

The largest group halved, but 8,835 rows is still **26% of the active
corpus**, so the decision below stands. Re-measure once more before
implementing: the location-parsing fix shipped the same day changes
`location`, which is part of the development key, so these figures will
move again on the next full crawl.

The wider lesson is worth keeping. Two of this ADR's motivating examples
were removed by the source site within a week of being measured. Numbers
taken from a live scraped corpus decay, and any figure quoted publicly —
on the homepage or anywhere else — needs a date attached and a re-check
before it is repeated.

## Investigated and rejected: collapsing on the shared image instead

The Kilimani 1-bed landing page shows the same rendered interior repeated
down the grid, which looked at first like a cheaper fix than this ADR —
merge on the perceptual hash and be done.

The shape is real. Those 223 listings are all BuyRentKenya, every one with
a distinct `image_url` and therefore a distinct `cluster_key`, but only
**6 distinct phashes**, two of which cover 174 listings. Grouping active
listings on `image_phash` + category + beds + price suggested 3,807
collapsible rows on BRK against 80 on P24.

**The asymmetry is not a missing feature.** Step 1.5 already matches
intra-source pairs on both sources. It groups each candidate by
`cluster_key` and skips any image spanning more than `CK_GROUP_CAP = 15`
distinct keys, on the reasoning that one image across many keys is a stock
or agency placeholder rather than one flat listed a few times. BRK gives
every listing its own `image_url`, so its cluster_keys never collapse and
these candidates blow past the cap. P24 reuses image URLs, so its keys
collapse and stay under it.

**And the guard is right.** Checking the three biggest hashes across the
whole corpus rather than inside one filtered page:

| phash | listings | suburbs | prices | bed counts | price range |
|---|---|---|---|---|---|
| `8c0633ccee316e3d` | 298 | 4 | 5 | 3 | 3.0M – 15.5M |
| `bbc6d8e432314e39` | 149 | 4 | 5 | 3 | 3.0M – 9.0M |
| `d8a543b43ce32778` | 140 | 2 | 3 | 2 | 3.0M – 6.0M |

One image spanning four suburbs, three bed counts and a five-fold price
range is an agency's generic render, not a photograph of a building.
Merging on it would weld unrelated flats together — precisely what
`CK_GROUP_CAP` prevents. The 3,807 figure is not a safe collapse, and the
claim that the hash route is "higher confidence" than the text key was
wrong.

This is the **third** time this project has mistaken a shared render for a
signal: `hide_generic_images` blanked 11% of properties and was disabled
after the groups turned out to be coherent developments; `CK_GROUP_CAP`
exists because a shared logo welded unrelated properties; and now this.
The recurring lesson is that **an image shared across many listings is
evidence of an agency's publishing habits, not of the building** — which
is the same conclusion this ADR reaches from the other direction when it
removes image similarity from the safeguard.

So the development key stands as written. No image-based shortcut replaces
it.

## Open: where this runs in the pipeline

The rule is settled; its position is not, and the choice matters.

`cluster_key` and the development key **cross-cut** — neither refines the
other. Two adverts can share an image and price but carry different
headlines, or share every word and differ in image. So a development group
may span several step-2 cluster properties, and a sequential "step 2b" that
joins each group to the first `property_id` it finds would leave the rest
unmerged, silently capping the collapse.

The correct construction is **union-find over all three edge types at once**
— step-1 scored pairs, shared `cluster_key`, shared development key — with
one property per connected component. Step 1 already builds union-find
groups, so the machinery exists, and it matches the standing rule that
dedup is evidence-weighted rather than a sequence of filters.

The cost is that it restructures `persist_dedup.py`, which has twice
emptied the site when it crashed mid-rebuild (26 and 27 August). That risk
argues for landing **build-then-swap before this**, not after.

One subtlety for whoever implements it: the size threshold must be applied
when the development edges are *created*, not to the finished components.
A component can grow past 4 by absorbing image edges, and letting that
satisfy the threshold would merge the pairs this ADR deliberately spares.

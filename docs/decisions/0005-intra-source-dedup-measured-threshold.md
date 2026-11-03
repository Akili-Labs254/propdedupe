# 0005: Intra-source dedup with a measured auto-merge threshold

**Status:** accepted (Phase 1, July 2026)

## Problem
`cluster_key` catches exact intra-source re-posts (identical image URL +
specs + price) but misses **re-uploads**: the same photo re-encoded
under a new URL. BRK had *no* intra-source dedup at all. Confirmed
live duplicates were reaching the consumer site.

## Approach
Triple-hash blocking within each source at threshold **≤ 4** (tighter
than cross-source's 8: the same CDN pipeline served both copies, so true
re-uploads sit at distance 0–2: the distance histogram confirmed 89%
of candidates at exactly 0, with almost nothing at 5+). Candidates are
scored by the same weighted scorer as cross-source pairs (standing
rule: evidence-weighted scoring, no new sequential filters).

## Auto-merge policy: set by measurement, not intuition
All 1,745 intra pairs initially shipped at review tier (no auto-merge),
because the feared false positive (sibling units in one development
sharing a render and identical specs) is invisible to the scorer.
A ~70-pair hand audit of the live queue (2026-07-15) measured:

| score band | audited | precision |
|-----------|---------|-----------|
| 0.00      | first 50 (of ~380) | 100% |
| 0.05–0.08 | 3 pages sampled | 100% |
| 0.15–0.19 | 4 pages sampled | ~97% |
| ~0.27     | 20 | ~90% |
| ~0.40     | 20 | 25% |

Findings: development renders never appeared in the low band (the
scorer's `distinct` tier had already absorbed them: 111 pairs); every
observed false positive carried `location ≥ 0.5`, which the scorer's
location gate already demotes out of auto-merge.

**Decision:** `INTRA_AUTO_CUT = 0.10`. Auto-merge below it (measured
100% precision, ~800 pairs); review from 0.10 to the review threshold;
the location gate stays in force. Revisit the cut only with a new audit.

## Known residual gap
Same property, *different* photos (day/night shots) is invisible to
single-image hashing: we store only the cover image hash. Fix is
multi-image hashing (`listing_images`, deferred since ADR 0002);
re-lists usually share at least one interior photo. Until then, the
admin merge tool covers this class.

## Side discovery
Cover-photo changes on source sites left stale hashes (upsert updates
`image_url`, hashes still described the old photo: visible as
"img 0.0" pairs with different pictures). Fixed by trigger:
`hash_invalidation.sql` nulls hashes on URL change; the refresh
pipeline's incremental stage re-hashes them.

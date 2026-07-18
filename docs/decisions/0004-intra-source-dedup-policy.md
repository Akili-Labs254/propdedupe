# 0004 — Intra-source dedup: thresholds set by measured precision

**Status:** accepted (Phase 1, July 2026)

## Problem
`cluster_key` (exact image URL + specs + price) catches exact re-posts
within a source, but misses **re-uploads**: the same photo re-encoded
under a new URL. Perceptual hashes match these; URLs don't. Cross-source
blocking never sees them because both listings are on the same site.

## Approach
1. **Blocking tighter than cross-source (Hamming ≤ 4 vs ≤ 8, 2-of-3
   hashes).** Within one source the same CDN pipeline served both
   copies, so true re-uploads sit at distance 0–2; the 5–8 band that
   cross-source needs (different portals' re-compression/watermarks)
   is empty intra-source. Distance histogram confirmed: 89% of
   candidates at distance 0, thin tail to 4.
2. **Same weighted scorer, no new rules** (evidence-weighted scoring,
   no single-field veto — standing rule).
3. **Auto-merge threshold set by a hand audit, not intuition.**
   First run capped everything at review tier; ~70 pairs were then
   hand-classified via the admin review UI (2026-07-15):

   | score band | audited | precision |
   |---|---|---|
   | 0.00 | pages 1–19 of queue (~380 pairs, 50 vetted) | ~100% |
   | 0.05–0.08 | 3 pages sampled | ~100% |
   | 0.15–0.19 | 4 pages sampled | ~97% |
   | ~0.27 | 20 pairs | ~90% |
   | ~0.40 | 20 pairs | 25% |

## Decision
- `INTRA_AUTO_CUT = 0.10`: intra pairs at or below auto-merge.
- 0.10 – 0.36 stays in the review queue (this is where every observed
  false positive lived).
- The scorer's location gate (loc ≥ 0.5 demotes auto-merge → review)
  stays on: all audited false positives carried loc 0.5.

## Notes
- The feared failure mode — development sibling units sharing renders —
  did not appear in the low band; spec disagreement already tiers those
  `distinct`.
- The audit surfaced a data bug: hashes weren't invalidated when a
  listing's cover photo changed (`img 0.0` next to visibly different
  photos). Fixed by trigger: `backend/db/hash_invalidation.sql`.
- Different-photo re-lists of the same house (e.g. day/night shots)
  remain uncatchable by single-cover hashing. Future fix: hash all
  listing photos (`listing_images`) and match on set overlap.

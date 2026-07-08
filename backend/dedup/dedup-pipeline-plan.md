d fixed when a freshly-scraped listing's Python-computed key matched an existing DB row (count ≥ 1).
 
**Standing rule this creates:** the Python and SQL key formulas must be kept byte-for-byte identical (field order, separator, decimal formatting, null-handling) — any future change to one must be mirrored in the other, or newly-scraped listings will silently stop matching their own historical duplicates.
 
**Finding 4 — unordered `.range()` pagination can silently corrupt a `set()`-based distinct count, even when the total row count looks correct.** Building the perceptual-hash backfill required fetching all distinct `image_url` values via paginated `.range()` calls (Supabase caps a single query at 1,000 rows). First version had no `.order()` clause before `.range()`. Result: total rows fetched across all batches was exactly correct (12,321, matching the known table size) — but distinct URLs computed from those rows came out as **4,496**, versus SQL's independently-verified **6,996** (confirmed via two separate checks: grouping by `length(image_url)`, and comparing `count(distinct image_url)` with/without stripping CDN query params — both ruled out data-side explanations).
 
Root cause: without an explicit sort, Postgres/PostgREST has no guaranteed stable ordering to slice against, so successive `.range(0,999)`, `.range(1000,1999)`, etc. calls are not guaranteed to return a clean, non-overlapping partition of the table. Some rows can be returned in more than one batch (harmless for a distinct count — duplicates don't add new values) while others can be skipped by every batch (silently invisible to the running `set()`, and this *does* lose real distinct values). Because skipped and duplicated rows happened to roughly balance out, the **total row count still looked correct**, masking the bug — only the distinct-value count revealed it.
 
Fix: add `.order("id")` immediately before `.range()` in every paginated fetch. This gives Postgres a fixed, deterministic sort to slice against, guaranteeing each row is fetched exactly once across all batches. Confirmed fixed when the Python distinct count matched SQL's exactly (6,996 = 6,996).
 
**Standing rule this creates:** any paginated fetch using `.range()` must include an explicit `.order()` on a unique column (e.g. `id`) beforehand. A correct total row count is *not* sufficient evidence that pagination is working correctly — only a correct distinct/content check catches this class of bug.

**Finding 5 — Cross-source pHash matching validates perceptual hashing as the anchor signal for BRK↔P24 dedup.**
Compared 1,124 distinct BRK hashes × 6,256 distinct P24 hashes (~7M pairs) via Hamming distance. Results at threshold ≤ 10: 58 candidate pairs (distance 6: 3 pairs, distance 8: 15, distance 10: 40).

Manual verification of distance-6 pairs — all three confirmed real cross-source duplicates:
- BRK 931 ↔ P24 8341: 5-bed Kileleshwa, KSh 70M on both sites.
- BRK 298 ↔ P24 2357/3210/17834: 4-bed Loresho, KSh 75M on both. P24 had it listed three times; BRK once.
- BRK 275 ↔ P24 1794: 6-bed Gigiri, KSh 150M on both sites.

Distance-8 pairs are mixed: several confirmed (5-bed Runda KSh 100M, 5-bed Runda KSh 190M, 9-bed Karen KSh 250M) but at least one false positive (BRK 801 ↔ P24 1232 — different houses on Kiambu Road with similar architectural style, different prices: KSh 60M vs 55M).

**Working threshold: 8.** Distance ≤ 6 appears highly reliable (3/3 confirmed). Distance 8 has real matches but also false positives — these need secondary signals (price, beds/baths, location) to disambiguate. Distance 10 is likely mostly noise (40 pairs, climbing sharply). The weighted scorer's job is to separate real matches from false positives in the 7–10 range using non-image signals.

**BRK baths are universally null** — the JSON-LD on BRK's results page doesn't include baths as a structured field. The data exists on BRK's detail pages (confirmed manually: listing 801 shows "5 Bathrooms" on its detail page). This is a data quality gap to fill before the weighted scorer runs, since baths agreement is a secondary signal.
 
## 6. Phase B: model comparison (future, conditional)
*(trigger criteria and scope to be defined once Phase A has verified labels)*
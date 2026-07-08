# PropDeDupe — Dedup Pipeline: Plan, Testing & Iteration Log

*Living document. Filled in stage by stage as the pipeline is designed, built, and tested — not written upfront and then coded to match.*

---

## 1. Purpose & scope

**Why this doc exists:** the dedup engine is the technical core of PropDeDupe and the part most worth a reviewer's attention. This doc is where the *reasoning* behind it lives — not just the code, but why each signal was chosen, why weights were set the way they were, how it was validated, and what broke along the way. The code shows *what* was built; this doc shows *how it was decided*.

**What we're trying to achieve:** given two independently-scraped sources (BuyRentKenya, Property24) plus intra-source duplication on P24 itself, reliably identify which listings represent the *same physical property*, without ever rehosting images or relying on a single field as a hard veto (per standing rule #5). Output should sort every compared pair into one of three tiers: **auto-merge**, **review queue**, or **distinct**.

**Phase A scope (this doc, for now):** a hand-reasoned, weighted heuristic scorer — no ML. Phase B (a learned model compared against this baseline) is out of scope until Phase A produces verified labels; see Section 6.

**What "done" looks like for Phase A:**
- Pipeline runs across the real stored dataset (BRK ~1,291 + P24 listings scraped so far), not just a toy sample.
- Every currently known duplicate cluster (documented in the project instructions' Scraping Recon sections) is caught at **auto-merge** or **review**, never scored as distinct.
- The review queue is a reasonably small fraction of total compared pairs — not thousands of pairs needing a human look, or the tier isn't doing its job.
- Each signal (image, geo, price, beds/baths, suburb adjacency, text) has documented reasoning for *why* it's weighted the way it is — not arbitrary numbers.
- A hand-verified sample of the review queue exists, so we have at least some pairs we're confident are correctly labeled (used later in Section 6 if a model is built).

**A physical constraint worth naming now, before signal design:** comparing every listing against every other listing is **O(n²)** — with ~13,000 total listings once P24 is fully scraped, that's on the order of 80+ million pairs. Computing five or six signals (some involving network calls, like lazy-fetched geo) for every one of those pairs isn't feasible on a laptop, or arguably ever, without a **candidate-generation ("blocking") step first** — some cheap, rough filter that narrows down which pairs are even worth scoring in detail, before the expensive per-pair signal computation runs. This will shape Section 2/3 directly: the pipeline isn't just "score every pair," it's "cheaply shortlist candidates, then score only those."

**A second constraint, discovered by comparing scraped volumes: P24 (~12,000+) vastly outnumbers BRK (~1,291).** This means blocking isn't really one problem, it's two differently-shaped ones:
- **Intra-P24** (same house re-listed multiple times on one site): the dominant case by volume, given how many more listings P24 has. Same-source formatting means exact image URL match is available as a strong, cheap blocking signal here (confirmed working intra-site per recon).
- **Cross-source (P24 ↔ BRK):** lower volume, different formatting between sources, no exact image URL match available (different CDNs) — only perceptual hash, which is more expensive to compute and compare at scale.

Section 2/3 should design blocking and possibly even weighting separately for these two cases rather than assuming one strategy fits both.

**Blocking step (resolved):** Cross-source blocking uses perceptual hash Hamming distance ≤ 10 as the candidate filter. With 1,124 distinct BRK hashes × 6,256 distinct P24 hashes = ~7M comparisons, brute-force is feasible (pure integer XOR + popcount, no network calls, completes in minutes). This produced 58 candidate pairs that then need scoring. Intra-P24 blocking uses `cluster_key` exact match (already complete — see Stage 1 results in Section 4).

---

## 2. Signal definitions

All signals output a **dissimilarity score** on a 0–1 scale (0 = perfect match, 1 = completely different). Null handling is per-signal — missing data is never penalised as a mismatch.

### 2a. Image pHash distance
Input: Hamming distance between two 64-bit perceptual hashes (0–10 range for candidates that passed blocking).
Formula: `distance / 10` (linear scaling to 0–1).
Null handling: N/A — candidates can't reach the scorer without a pHash.

### 2b. Price distance
Input: two listing prices.
Formula: `abs(a - b) / max(a, b)` — symmetric percentage gap.
Cap: any gap ≥ 20% maps to 1.0. Rationale: agents test different prices on different portals (confirmed: KSh 55M vs 60M = 8.7% on a real cross-source pair), so small gaps are normal; but a 20%+ gap almost certainly means different properties.
Null handling: either price null → 0.5 (uncertain, not penalised or rewarded).

### 2c. Bedrooms
Input: two bedroom counts (integers).
Formula: `min(abs(a - b), 3) / 3`. 0 diff → 0.0, 1 diff → 0.33, 2 diff → 0.67, 3+ diff → 1.0.
Null handling: either null → 0.3 (mild uncertainty).

### 2d. Baths
Input: two bath counts (float, supports half-baths).
Formula: `min(abs(a - b), 3) / 3`. Same scale as bedrooms.
Null handling: either null → 0.0 (neutral). BRK baths are null ~11% of the time; penalising would bias against BRK matches unfairly.

### 2e. Location (suburb)
Input: two suburb name strings.
Three buckets: same suburb (case-insensitive) → 0.0; adjacent suburbs (per adjacency map) → 0.3; non-adjacent → 0.7.
Why not 1.0 for non-adjacent: same house has been observed listed under 3 different suburbs (Westlands/Loresho/Mountain View), so even non-adjacent doesn't guarantee different properties — just less likely.
Null handling: either null → 0.3 (mild uncertainty, same as adjacent).

**Suburb adjacency map (Nairobi, initial):**
Lavington ↔ Kilimani, Westlands ↔ Loresho ↔ Mountain View, Karen ↔ Lang'ata, Runda ↔ Gigiri, Garden Estate ↔ Thome, Kileleshwa ↔ Riverside. Expanded as real data reveals more overlaps.

---

## 3. Weighting & tiers

### Weights (sum to 1.0)
| Signal    | Weight | Rationale |
|-----------|--------|-----------|
| pHash     | 0.35   | Anchor signal; already pre-filtered by blocking step |
| Price     | 0.25   | Strongest factual discriminator after image |
| Bedrooms  | 0.20   | Hard to mis-scrape, strong negative signal when mismatched |
| Baths     | 0.10   | Often null on BRK side, lower confidence |
| Location  | 0.10   | Unreliable as negative signal (suburbs overlap empirically) |

### Tier thresholds (dissimilarity scale — lower = more similar)
| Tier        | Score range | Action |
|-------------|-------------|--------|
| Auto-merge  | < 0.30      | Same property, merge automatically |
| Review      | 0.30 – 0.50 | Probable match, needs human verification |
| Distinct    | > 0.50      | Different properties |

### Validation against known pairs
- **5-bed Kileleshwa KSh 70M** (dist 6, all fields match): score 0.21 → auto-merge ✓
- **4-bed Loresho KSh 75M** (dist 6, all fields match): score 0.21 → auto-merge ✓
- **6-bed Gigiri KSh 150M** (dist 6, all fields match): score 0.21 → auto-merge ✓
- **Kiambu Road false positive** (dist 8, KSh 55M vs 60M, different houses): score 0.39 → review ✓
- Thresholds will be adjusted once the scorer runs across all candidates and results are spot-checked.

---

## 4. Baseline validation
*(to be filled in once there's a scorer to validate)*

**First quantified Stage 1 result (12,321 P24 listings).**
Grouping key `image_url + bedrooms + baths + size_sqm + price` (no location): **4,427 of 12,321 listings collapse as redundant (~36% bloat)** → ~7,894 distinct products. This is the first hard dedup number, defensible for README/interview ("P24's Nairobi house listings are ~36% redundant; PropDeDupe collapses them to ~7,900 distinct products").

**Validation finding — location must NOT be in the grouping key.** Adding `location` to the key *reduced* collapse from 4,427 to 4,266 — meaning ~161 genuine duplicates got split apart because the same physical house is listed under multiple suburbs (Westlands / Loresho / Mountain View). This is direct empirical confirmation of standing rule #8 ("location is a dedup score, never a blocking key"). The correct number is the location-free 4,427.

**Caveat on the key — it's effectively 4 fields, not 5.** `size_sqm` is `null` for nearly all P24 result-page tiles (the site rarely exposes floor size on the listing card). So the distinguishing work is really done by `image_url + bedrooms + baths + price`; sqm contributes only on the rare rows where it's populated. Not a bug, but worth knowing the key is thinner in practice than on paper.

**Top clusters are developments, and collapsing them is correct behaviour.** The largest cluster (74 listings: Katani, 3bed/4bath, KSh 10.5M) is the Eden Villas / Savannah Park development. Per Finding 2 above, the grouping can't distinguish "74 real identical units" from "1 house relisted 74 times" — but for the aggregator both should show once, so a 74→1 collapse is intended, not an error.

---

## 5. Findings & iteration log
*(running log, updated as we go)*

### 5a. Errors & edge cases

**Finding 1 — `GROUP BY image_url` alone massively over-clusters (developments defeat it).**
First run of the Stage 1 query on 12,321 real P24 listings returned clusters far larger than expected: top clusters of **74, 65, 61, 61, 45, 38...** listings sharing one exact image URL. Hand-found recon examples were clusters of 2–3, so these large clusters are not 74 re-listings of one house.

Manual investigation (opening the actual listings) revealed the cause: **standardized developments.** A development like "Eden Villas" or "Savannah Park" contains many identical units (e.g. "four courts of twelve homes" = 48 units, or "nine luxury homes"). Every unit of the same type shares the same marketing photo, the same price, and the same beds/baths/sqm **by design** — because they are genuinely identical floor plans. So image match + identical price + identical beds/baths does NOT prove duplication here; a development defeats all of those signals simultaneously.

This directly confirms standing rule #6 ("image match is not auto-conclusive: developments reuse renders") — and extends it: image match is not conclusive even when *combined* with price/beds/baths, because a development holds all of those constant across many distinct real units.

**Finding 2 — the real bloat is intra-type re-listing, and the fields we already store are enough to collapse it.**
Refined the mental model with real examples: within a development, the distinct *products* are type variants (A, B, C) which genuinely differ in beds/baths/sqm/price. But agencies post every *instance* of a type as its own listing — A1, A2, ... A10 — all identical on every field a buyer can see. From a buyer's search perspective these are indistinguishable bloat; one listing for "Type A in Eden Villas" is all that's useful.

Key consequence: **we do not need address parsing or unit-count regex to collapse this.** The fields already stored — `image_url` + `bedrooms` + `baths` + `size_sqm` + `price` — already distinguish type A from B/C (they differ on beds/baths/sqm) while collapsing A1–A10 (identical on all). Grouping on those existing fields does the work. (An earlier idea to regex the stated unit count out of the description, or to lean on the `address` column as the primary differentiator, was considered and dropped as over-engineering — the stored numeric fields are sufficient and simpler.)

**Honest caveat on Finding 2:** this grouping cannot by itself distinguish "10 real identical units in a development" from "1 house spam-relisted 10 times" — both collapse into one group. But **for the aggregator's purpose this distinction may not matter**: either way the buyer wants to see the product once (optionally with a "N units available" note), so collapsing serves the buyer identically. The distinction only matters for counting true housing inventory, which is not the product goal. `address` was still added to the schema (cheap now, useful later for precise geocoding / Phase 2), but it is a nice-to-have detail, not load-bearing for Stage 1 grouping.

**Finding 3 — `cluster_key` computed in Python must match SQL's `md5()` byte-for-byte, and float→string casting is the trap.** `cluster_key` (an `md5` hash of `image_url|bedrooms|baths|size_sqm|price`) was first backfilled via SQL, then a Python helper (`compute_cluster_key()` in `save.py`) was added so new scrapes compute it at save time. First attempt produced a key that did **not** match any existing row (count = 0), despite using the identical field values. Cause: Postgres's `numeric(x,y)::text` cast always renders the column's *declared decimal scale* (e.g. `numeric(3,1)` → `"4.0"`, `numeric(10,2)` → `"10500000.00"`), but Python's plain `str()` on a float does not know that scale (`str(4.0)` → `"4.0"` is fine, but inconsistent rounding/trailing-zero behaviour across values caused mismatches). Fixed by formatting Python's numeric fields with explicit decimal places matching each column's scale (`f"{baths:.1f}"`, `f"{price:.2f}"`, `f"{size_sqm:.2f}"`) rather than relying on `str()`. Confirmed fixed when a freshly-scraped listing's Python-computed key matched an existing DB row (count ≥ 1).

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

**Finding 6 — BRK detail pages expose structured baths and URL via JSON-LD, despite results pages dropping listing data.**
BRK changed their results-page JSON-LD (no longer contains `mainEntity.itemListElement` — now site-level metadata only). However, detail pages still embed a rich `@graph` with `Accommodation` (`numberOfBathroomsTotal`, `numberOfBedrooms`) and `Offer` (`url`) nodes. Backfill script fetches `buyrentkenya.com/listings/item-XXXXXXX` (redirects to real slug URL), parses the JSON-LD, and updates `baths` + `url`. Result: 1,184/1,206 BRK listings now have baths populated; ~20 returned no data (likely delisted); 1 network timeout. The `baths IS NULL` filter makes the script idempotent — safe to re-run for the stragglers.

---

## 6. Phase B: model comparison (future, conditional)
*(trigger criteria and scope to be defined once Phase A has verified labels)*
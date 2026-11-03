# PropDeDupe dedup pipeline: plan, testing and iteration log

*Working log from the initial build (July 2026), kept as written. Numbers are from the first ~13.5k-listing corpus, not the current one. Later changes are in `docs/decisions/`.*

---

## 1. Purpose & scope

**Why this doc exists:** the dedup engine is the technical core of PropDeDupe and the part most worth a reviewer's attention. This doc is where the *reasoning* behind it lives, not just the code, but why each signal was chosen, why weights were set the way they were, how it was validated, and what broke along the way. The code shows *what* was built; this doc shows *how it was decided*.

**What we're trying to achieve:** given two independently-scraped sources (BuyRentKenya, Property24) plus intra-source duplication on P24 itself, reliably identify which listings represent the *same physical property*, without ever rehosting images or relying on a single field as a hard veto (per standing rule #5). Output should sort every compared pair into one of three tiers: **auto-merge**, **review queue**, or **distinct**.

**Phase A scope (this doc, for now):** a hand-reasoned, weighted heuristic scorer, no ML. Phase B (a learned model compared against this baseline) is out of scope until Phase A produces verified labels; see Section 6.

**What "done" looks like for Phase A:**
- Pipeline runs across the real stored dataset (BRK ~1,291 + P24 listings scraped so far), not just a toy sample.
- Every currently known duplicate cluster (documented in the project instructions' Scraping Recon sections) is caught at **auto-merge** or **review**, never scored as distinct.
- The review queue is a reasonably small fraction of total compared pairs, not thousands of pairs needing a human look, or the tier isn't doing its job.
- Each signal (image, geo, price, beds/baths, suburb adjacency, text) has documented reasoning for *why* it's weighted the way it is, not arbitrary numbers.
- A hand-verified sample of the review queue exists, so we have at least some pairs we're confident are correctly labeled (used later in Section 6 if a model is built).

**A physical constraint worth naming now, before signal design:** comparing every listing against every other listing is **O(n²)**: with ~13,000 total listings once P24 is fully scraped, that's on the order of 80+ million pairs. Computing five or six signals (some involving network calls, like lazy-fetched geo) for every one of those pairs isn't feasible on a laptop, or arguably ever, without a **candidate-generation ("blocking") step first**, some cheap, rough filter that narrows down which pairs are even worth scoring in detail, before the expensive per-pair signal computation runs. This will shape Section 2/3 directly: the pipeline isn't just "score every pair," it's "cheaply shortlist candidates, then score only those."

**A second constraint, discovered by comparing scraped volumes: P24 (~12,000+) vastly outnumbers BRK (~1,291).** This means blocking isn't really one problem, it's two differently-shaped ones:
- **Intra-P24** (same house re-listed multiple times on one site): the dominant case by volume, given how many more listings P24 has. Same-source formatting means exact image URL match is available as a strong, cheap blocking signal here (confirmed working intra-site per recon). Uses `cluster_key` exact match: no perceptual hashing needed.
- **Cross-source (P24 ↔ BRK):** lower volume, different formatting between sources, no exact image URL match available (different CDNs), only perceptual hash comparison works here.

### Blocking step (resolved)

**Intra-P24:** `cluster_key` exact match (md5 of `image_url|bedrooms|baths|size_sqm|price`). Already complete: collapses 36% of P24 listings (see Section 4).

**Cross-source:** triple-hash blocking using `min(phash, ahash, whash)` Hamming distance with **2-of-3 agreement** rule. Each BRK hash triple is compared against each P24 hash triple; a pair passes blocking only if at least 2 of the 3 hash algorithms score at or below threshold 8. With ~1,127 distinct BRK hash triples × ~6,256 distinct P24 hash triples = ~7M comparisons, brute-force is feasible (pure integer XOR + popcount, no network calls, completes in minutes).

The 2-of-3 rule was adopted after initial `min()` alone produced too many false positives: a single hash algorithm can fluke a low distance on structurally similar but different houses. Requiring 2 algorithms to agree filters this noise.

---

## 2. Signal definitions

All signals output a **dissimilarity score** on a 0–1 scale (0 = perfect match, 1 = completely different). Null handling is per-signal: missing data is never penalised as a mismatch.

### 2a. Image distance (triple-hash)
Input: minimum Hamming distance across phash, ahash, and whash for a candidate pair that passed blocking.
Formula: `min_distance / 8` (linear scaling to 0–1, where 8 = blocking threshold).
Why three hashes: watermarks (P24 "property24" corner stamp, agency stamps like "Geoscape Global", "Afrique Properties Limited") inflate any single hash's distance by 4–6 bits. Different watermark styles trip different algorithms: phash handles some better, whash/ahash handle others. Taking the minimum across three algorithms reliably scores ≤ 7 for confirmed matches and ≥ 10 for confirmed false positives (see Finding 7).
Null handling: N/A. Candidates can't reach the scorer without all three hashes.

### 2b. Price distance
Input: two listing prices.
Formula: `abs(a - b) / max(a, b)`, symmetric percentage gap.
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

### 2e. Location (suburb adjacency)
Uses `backend/dedup/adjacency.py`. Two layers:
1. **Name normalization:** strips prefixes (Off/Near/Along), expands abbreviations (Rd→Road), maps ~70 BRK street/estate names to canonical P24 suburbs. Sources: P24 suburb suggestions API (`/search/suburbsuggestions?cityid=1890`), BRK `SELECT DISTINCT location` dump, local Nairobi knowledge.
2. **Adjacency graph:** 78 Nairobi suburbs, 100 edges. Built from P24 address hierarchy, Wikipedia geographic borders, sub-county ward groupings, and pipeline false-positive analysis (Karen↔Runda gap, South C↔Syokimau gap).

Four-tier output: same suburb (0.0), adjacent (0.3), known-but-far (0.5), unknown (0.8). Unknown scores 0.8 not 1.0: per rule #5, no single field vetoes.
Null handling: either null → 0.8 (unknown).

**Location gate:** if location_score >= 0.5, pair cannot auto-merge (goes to review at minimum). Prevents visually similar houses in distant suburbs from merging.

---

## 3. Weighting & tiers

### Current weights (v4: sum to 1.0)
| Signal    | Weight | Rationale |
|-----------|--------|-----------|
| Image     | 0.30   | Anchor signal; already pre-filtered by blocking step |
| Price     | 0.25   | Strongest factual discriminator after image |
| Bedrooms  | 0.20   | Hard to mis-scrape, strong negative signal when mismatched |
| Baths     | 0.05   | Most arbitrarily listed signal: agents frequently omit or miscount |
| Location  | 0.20   | Adjacency map now reliable (78 suburbs, 100 edges, ~70 BRK aliases) |

### Tier thresholds (dissimilarity scale: lower = more similar)
| Tier        | Score range | Action |
|-------------|-------------|--------|
| Auto-merge  | < 0.36      | Same property, merge automatically |
| Review      | 0.36 – 0.50 | Probable match, needs human verification |
| Distinct    | > 0.50      | Different properties |

Location gate applied post-scoring: location >= 0.5 forces at least review tier.

### Weight evolution
- v1: image 0.35, price 0.25, beds 0.20, baths 0.10, location 0.10. Location too unreliable to weight higher: adjacency map was reactive/incomplete.
- v2: same weights, auto-merge threshold raised 0.30→0.32→0.36 (see threshold evolution below).
- v3: image 0.35→0.30, baths 0.10→0.05, location 0.10→0.15. Adjacency map rebuilt with normalization.
- v4 (current): location 0.15→0.20. Adjacency map validated as reliable. Location gate added.

### Threshold evolution
- v1: auto-merge < 0.30. Too tight: confirmed 5-bed Runda KSh 100M match scored 0.313 (1-bath difference between sources).
- v2: auto-merge < 0.32. Caught the Runda pair. Still missed distance-10 pairs where all other fields matched.
- v3 (current): auto-merge < 0.36. Catches "high image distance but everything else matches" pairs. Validated against hand-checked results (see Section 4).

### Current validation results (v4 weights + location gate)
- **~120 auto-merge:** ~5 false positives (~96% precision). Down from 13 FP at v1 weights. Remaining FPs mostly structurally similar modern houses with very low image distance that happen to share beds/price.
- **~116 review:** mix of true matches and false positives. Review queue filters at API query time: pairs where either listing already has a `property_id` are hidden (already resolved).
- **~830 distinct:** spot-checked ~50, found 1 missed match. Most correctly rejected.

---

## 4. Baseline validation

**First quantified Stage 1 result (12,321 P24 listings).**
Grouping key `image_url + bedrooms + baths + size_sqm + price` (no location): **4,427 of 12,321 listings collapse as redundant (~36% bloat)** → ~7,894 distinct products. This was the first hard dedup number.

**Validation finding: location must NOT be in the grouping key.** Adding `location` to the key *reduced* collapse from 4,427 to 4,266, meaning ~161 genuine duplicates got split apart because the same physical house is listed under multiple suburbs (Westlands / Loresho / Mountain View). This is direct empirical confirmation of standing rule #8 ("location is a dedup score, never a blocking key"). The correct number is the location-free 4,427.

**Caveat on the key: it's effectively 4 fields, not 5.** `size_sqm` is `null` for nearly all P24 result-page tiles (the site rarely exposes floor size on the listing card). So the distinguishing work is really done by `image_url + bedrooms + baths + price`; sqm contributes only on the rare rows where it's populated. Not a bug, but worth knowing the key is thinner in practice than on paper.

**Top clusters are developments, and collapsing them is correct behaviour.** The largest cluster (74 listings: Katani, 3bed/4bath, KSh 10.5M) is the Eden Villas / Savannah Park development. Per Finding 2 above, the grouping can't distinguish "74 real identical units" from "1 house relisted 74 times", but for the aggregator both should show once, so a 74→1 collapse is intended, not an error.

**Note on intra-P24 vs cross-source:** Stage 1 (cluster_key) handles intra-P24 dedup using exact field matching, no perceptual hashing needed since same-source listings share exact image URLs. Cross-source dedup (BRK ↔ P24) requires perceptual hashing because different CDNs, image sizes, and watermarks prevent exact matching. These are complementary, not overlapping, cluster_key catches the bulk volume (36% of P24), triple-hash catches the cross-source overlap (~100–150 candidate pairs).

---

## 5. Findings & iteration log
*(running log, updated as we go)*

### 5a. Errors & edge cases

**Finding 1: `GROUP BY image_url` alone massively over-clusters (developments defeat it).**
First run of the Stage 1 query on 12,321 real P24 listings returned clusters far larger than expected: top clusters of **74, 65, 61, 61, 45, 38...** listings sharing one exact image URL. Cause: **standardized developments** reuse one marketing photo across many genuinely-distinct identical units. Image match is not conclusive even when *combined* with price/beds/baths, because a development holds all of those constant across many distinct real units.

**Finding 2: the real bloat is intra-type re-listing, and the fields we already store are enough to collapse it.**
Within a development, agencies post every *instance* of a type as its own listing (A1, A2, ... A10), all identical on every buyer-visible field. Grouping on `image_url + bedrooms + baths + size_sqm + price` collapses these while keeping type variants separate. No address parsing needed.

**Finding 3: `cluster_key` computed in Python must match SQL's `md5()` byte-for-byte, and float→string casting is the trap.** Fixed by formatting with explicit decimal places matching each column's scale.

**Standing rule:** the Python and SQL key formulas must be kept byte-for-byte identical.

**Finding 4: unordered `.range()` pagination can silently corrupt a `set()`-based distinct count, even when the total row count looks correct.** Fixed by adding `.order("id")` before `.range()`.

**Standing rule:** any paginated fetch using `.range()` must include an explicit `.order()` on a unique column beforehand.

**Finding 5: Cross-source pHash matching validates perceptual hashing as the anchor signal for BRK↔P24 dedup.**
Compared 1,124 × 6,256 = ~7M hash pairs. 58 candidates at threshold ≤ 10. All distance-6 pairs confirmed real matches. Distance-8 mixed.

**Finding 6: BRK detail pages expose structured baths and URL via JSON-LD, despite results pages dropping listing data.**
Detail pages still embed `Accommodation` (`numberOfBathroomsTotal`) and `Offer` (`url`) nodes. 1,184/1,206 BRK listings backfilled.

**Finding 7: Watermarks inflate pHash distances; triple-hash min(phash, ahash, whash) gives clean separation.**
- dhash unusable (dropped). phash alone can't separate matches from false positives at the boundary.
- `min(phash, ahash, whash)`: matches max at 7, false positives min at 10. Threshold 8 works.
- **2-of-3 agreement rule:** requiring 2 hashes to agree at ≤8 filters noise from structurally similar but different houses.

**Finding 8: agents list the same property with wildly different metadata across portals.**
Confirmed matches hiding in distinct tier: same house with 35% price gap, different suburb names, different bedroom counts. Image matching catches what field-based signals never would.

**Finding 9: BRK uses street-level locations, P24 uses suburb-level.**
BRK locations are mostly road/estate names ("Chalbi Drive", "Runda Mimosa"). P24 uses suburb names ("Lavington", "Runda"). Fix: ~70 alias mappings in `backend/dedup/adjacency.py` mapping BRK roads/estates → containing P24 suburb. Covers ~80% of BRK locations.

Two alias gaps identified post-validation: `'old runda': 'runda'` and `'loresho ridge road': 'loresho'`.

**Finding 10: Persist step: properties, matches, and listing links.**
`scripts/persist_dedup.py` writes pipeline results to three DB targets:

1. **`matches` table**: all 1,047 scored pairs with tier, total score, and per-signal scores. Includes auto-merge, review, AND distinct pairs. Audit trail: never deleted. API filters at query time (hide pairs where either listing already has a property_id).

2. **`properties` table**: one row per deduplicated physical property. Currently 78 rows from cross-source auto-merge. Representative chosen by: BRK first (richer titles), then most non-null fields. `source_count` tracks how many sources list this property.

3. **`listings.property_id`**: FK linking each listing to its property. Multiple listings → same property_id = duplicates. NULL = not yet processed.

Uses Union-Find (connected components) to group transitive matches: if A↔B and B↔C both auto-merge, all three get the same property_id. Script is re-runnable: clears in FK-safe order before inserting.

**All three persist steps are complete:**
- Step 1: cross-source auto-merge → 78 properties from 101 pairs (299 listings linked)
- Step 2: intra-P24 cluster_key groups → 1,253 properties (5,607 listings linked). Clusters with a member already linked from Step 1 join the existing property.
- Step 3: remaining unmatched listings → 7,621 individual properties

Final result: 13,527 listings → 8,952 properties. Every listing has a property_id. The properties table is the complete deduplicated index the API serves from.

**DB migration note:** tables created via SQL Editor require explicit `GRANT SELECT, INSERT, UPDATE, DELETE ON public.matches TO anon` + sequence grant (same gotcha as dashboard vs SQL Editor table creation).

---

## 6. Phase B: future improvements (conditional)

### 6a. SSIM verification (conditional: if review queue stays noisy)
SSIM compares images at full resolution. Too expensive for blocking (~7M pairs), viable as a second-opinion signal on ~100–200 candidate pairs. Trigger: review queue precision below 50%.

### 6b. Embedding-based matching (future: when same-house-different-photo cases emerge)
Neural embeddings (e.g. CLIP) enable matching even when agents use completely different photos. Beyond what perceptual hashing can do. Trigger: confirmed same-house-different-photo cases. Worth mentioning in README as designed upgrade path.

### 6c. Learned model (future)
Hand-verified pairs provide labeled training data. Small classifier could learn optimal weights from data. Only if heuristic scorer plateaus. The heuristic scorer remains the baseline to beat.

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
- **Intra-P24** (same house re-listed multiple times on one site): the dominant case by volume, given how many more listings P24 has. Same-source formatting means exact image URL match is available as a strong, cheap blocking signal here (confirmed working intra-site per recon). Uses `cluster_key` exact match — no perceptual hashing needed.
- **Cross-source (P24 ↔ BRK):** lower volume, different formatting between sources, no exact image URL match available (different CDNs) — only perceptual hash comparison works here.

### Blocking step (resolved)

**Intra-P24:** `cluster_key` exact match (md5 of `image_url|bedrooms|baths|size_sqm|price`). Already complete — collapses 36% of P24 listings (see Section 4).

**Cross-source:** triple-hash blocking using `min(phash, ahash, whash)` Hamming distance with **2-of-3 agreement** rule. Each BRK hash triple is compared against each P24 hash triple; a pair passes blocking only if at least 2 of the 3 hash algorithms score at or below threshold 8. With ~1,127 distinct BRK hash triples × ~6,256 distinct P24 hash triples = ~7M comparisons, brute-force is feasible (pure integer XOR + popcount, no network calls, completes in minutes).

The 2-of-3 rule was adopted after initial `min()` alone produced too many false positives — a single hash algorithm can fluke a low distance on structurally similar but different houses. Requiring 2 algorithms to agree filters this noise.

---

## 2. Signal definitions

All signals output a **dissimilarity score** on a 0–1 scale (0 = perfect match, 1 = completely different). Null handling is per-signal — missing data is never penalised as a mismatch.

### 2a. Image distance (triple-hash)
Input: minimum Hamming distance across phash, ahash, and whash for a candidate pair that passed blocking.
Formula: `min_distance / 8` (linear scaling to 0–1, where 8 = blocking threshold).
Why three hashes: watermarks (P24 "property24" corner stamp, agency stamps like "Geoscape Global", "Afrique Properties Limited") inflate any single hash's distance by 4–6 bits. Different watermark styles trip different algorithms — phash handles some better, whash/ahash handle others. Taking the minimum across three algorithms reliably scores ≤ 7 for confirmed matches and ≥ 10 for confirmed false positives (see Finding 7).
Null handling: N/A — candidates can't reach the scorer without all three hashes.

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

**Suburb adjacency map — current state and known gaps:**
Current map covers: Lavington ↔ Kilimani, Westlands ↔ Loresho ↔ Mountain View, Karen ↔ Lang'ata ↔ Hardy ↔ Karen Hardy ↔ Miotoni Road ↔ Kerarapon Road ↔ KCB Leadership, Runda ↔ Gigiri ↔ Kiambu Road ↔ Nyari, Garden Estate ↔ Thome ↔ Ridgeways ↔ Ridgeway, Kileleshwa ↔ Riverside, South C ↔ Mombasa Rd, Kitengela Road ↔ Lang'ata, Kyuna ↔ Kiambu Road, Off Loresho Ridge Road ↔ Loresho.

**Known gaps (cause false positives in current run):** agents use inconsistent suburb names with prefixes (Off, Near, Along), abbreviations (Rd/Road), and sub-area names that don't match the canonical suburb. The adjacency map needs two improvements: (1) name normalization (strip prefixes, collapse abbreviations) before comparison, and (2) a geography-driven rebuild rather than reactive patching. Planned for next session.

---

## 3. Weighting & tiers

### Weights (sum to 1.0)
| Signal    | Weight | Rationale |
|-----------|--------|-----------|
| Image     | 0.35   | Anchor signal; already pre-filtered by blocking step |
| Price     | 0.25   | Strongest factual discriminator after image |
| Bedrooms  | 0.20   | Hard to mis-scrape, strong negative signal when mismatched |
| Baths     | 0.10   | Often null on BRK side, lower confidence |
| Location  | 0.10   | Unreliable as negative signal (suburbs overlap empirically) |

*Note: location weight may increase to 0.15 (taking 0.05 from image) once the adjacency map is properly built. Currently too unreliable to weight higher — false non-adjacency would penalise real matches.*

### Tier thresholds (dissimilarity scale — lower = more similar)
| Tier        | Score range | Action |
|-------------|-------------|--------|
| Auto-merge  | < 0.36      | Same property, merge automatically |
| Review      | 0.36 – 0.50 | Probable match, needs human verification |
| Distinct    | > 0.50      | Different properties |

### Threshold evolution
- v1: auto-merge < 0.30. Too tight — confirmed 5-bed Runda KSh 100M match scored 0.313 (1-bath difference between sources).
- v2: auto-merge < 0.32. Caught the Runda pair. Still missed distance-10 pairs where all other fields matched.
- v3 (current): auto-merge < 0.36. Catches "high image distance but everything else matches" pairs. Validated against hand-checked results (see Section 4).

### Current validation results (triple-hash blocking, latest run)
- **116 auto-merge:** 103 verified correct (~89% precision). 13 false positives — mostly caused by structurally similar modern houses scoring low on triple-hash despite being different properties. Most would be fixed by a proper adjacency map (Karen↔Runda pairs incorrectly scored as non-adjacent scored just under threshold due to very low image distance).
- **82 review:** 27 verified correct matches (~33% true positive rate). Remainder are false positives with borderline image similarity.
- **849 distinct:** spot-checked ~50, found 1 missed match (very low precision loss). Most are correctly rejected.

---

## 4. Baseline validation

**First quantified Stage 1 result (12,321 P24 listings).**
Grouping key `image_url + bedrooms + baths + size_sqm + price` (no location): **4,427 of 12,321 listings collapse as redundant (~36% bloat)** → ~7,894 distinct products. This is the first hard dedup number, defensible for README/interview ("P24's Nairobi house listings are ~36% redundant; PropDeDupe collapses them to ~7,900 distinct products").

**Validation finding — location must NOT be in the grouping key.** Adding `location` to the key *reduced* collapse from 4,427 to 4,266 — meaning ~161 genuine duplicates got split apart because the same physical house is listed under multiple suburbs (Westlands / Loresho / Mountain View). This is direct empirical confirmation of standing rule #8 ("location is a dedup score, never a blocking key"). The correct number is the location-free 4,427.

**Caveat on the key — it's effectively 4 fields, not 5.** `size_sqm` is `null` for nearly all P24 result-page tiles (the site rarely exposes floor size on the listing card). So the distinguishing work is really done by `image_url + bedrooms + baths + price`; sqm contributes only on the rare rows where it's populated. Not a bug, but worth knowing the key is thinner in practice than on paper.

**Top clusters are developments, and collapsing them is correct behaviour.** The largest cluster (74 listings: Katani, 3bed/4bath, KSh 10.5M) is the Eden Villas / Savannah Park development. Per Finding 2 above, the grouping can't distinguish "74 real identical units" from "1 house relisted 74 times" — but for the aggregator both should show once, so a 74→1 collapse is intended, not an error.

**Note on intra-P24 vs cross-source:** Stage 1 (cluster_key) handles intra-P24 dedup using exact field matching — no perceptual hashing needed since same-source listings share exact image URLs. Cross-source dedup (BRK ↔ P24) requires perceptual hashing because different CDNs, image sizes, and watermarks prevent exact matching. These are complementary, not overlapping — cluster_key catches the bulk volume (36% of P24), triple-hash catches the cross-source overlap (~100–150 candidate pairs).

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

Key consequence: **we do not need address parsing or unit-count regex to collapse this.** The fields already stored — `image_url` + `bedrooms` + `baths` + `size_sqm` + `price` — already distinguish type A from B/C (they differ on beds/baths/sqm) while collapsing A1–A10 (identical on all). Grouping on those existing fields does the work.

**Honest caveat on Finding 2:** this grouping cannot by itself distinguish "10 real identical units in a development" from "1 house spam-relisted 10 times" — both collapse into one group. But **for the aggregator's purpose this distinction may not matter**: either way the buyer wants to see the product once (optionally with a "N units available" note), so collapsing serves the buyer identically.

**Finding 3 — `cluster_key` computed in Python must match SQL's `md5()` byte-for-byte, and float→string casting is the trap.** Fixed by formatting Python's numeric fields with explicit decimal places matching each column's scale (`f"{baths:.1f}"`, `f"{price:.2f}"`, `f"{size_sqm:.2f}"`).

**Standing rule:** the Python and SQL key formulas must be kept byte-for-byte identical — any future change to one must be mirrored in the other.

**Finding 4 — unordered `.range()` pagination can silently corrupt a `set()`-based distinct count, even when the total row count looks correct.** Fixed by adding `.order("id")` before `.range()` in every paginated fetch.

**Standing rule:** any paginated fetch using `.range()` must include an explicit `.order()` on a unique column beforehand.

**Finding 5 — Cross-source pHash matching validates perceptual hashing as the anchor signal for BRK↔P24 dedup.**
Compared 1,124 distinct BRK hashes × 6,256 distinct P24 hashes (~7M pairs) via Hamming distance. Results at threshold ≤ 10: 58 candidate pairs (distance 6: 3 pairs, distance 8: 15, distance 10: 40).

Manual verification of distance-6 pairs — all three confirmed real cross-source duplicates:
- BRK 931 ↔ P24 8341: 5-bed Kileleshwa, KSh 70M on both sites.
- BRK 298 ↔ P24 2357/3210/17834: 4-bed Loresho, KSh 75M on both.
- BRK 275 ↔ P24 1794: 6-bed Gigiri, KSh 150M on both sites.

Distance-8 pairs are mixed: several confirmed, at least one false positive. Working threshold: 8 for phash alone.

**Finding 6 — BRK detail pages expose structured baths and URL via JSON-LD, despite results pages dropping listing data.**
BRK changed their results-page JSON-LD (no longer contains listing data). Detail pages still embed `Accommodation` (`numberOfBathroomsTotal`) and `Offer` (`url`) nodes. Backfill result: 1,184/1,206 BRK listings now have baths populated. The `baths IS NULL` filter makes the script idempotent.

**Finding 7 — Watermarks inflate pHash distances; triple-hash min(phash, ahash, whash) gives clean separation between matches and false positives.**
Tested phash, ahash, dhash, and whash across 8 confirmed cross-source matches and 3 confirmed false positives.

- **dhash** is unusable (worst on every pair, 11–16 on matches). Dropped.
- **phash alone** cannot separate matches from false positives — both score 10 at the boundary.
- **ahash** excels on some pairs (scored 1 on Gigiri, 2 on Kileleshwa) but inconsistent (9 on Loresho).
- **whash** excels on different pairs (scored 2 on Braeburn, 4 on Runda 160M) but also inconsistent alone.
- **min(phash, ahash, whash)** gives clean separation:
  - Matches: max min-distance = 7 (across 8 verified pairs)
  - False positives: min min-distance = 10 (across 3 verified pairs)
  - Gap: 7 to 10 — threshold of 8 catches all matches, rejects all false positives.

Cropping (bottom 10–20%) was tested first and made distances *worse* — watermarks are center-overlaid, not bottom-only.

**2-of-3 agreement rule:** initial implementation using bare `min()` across three hashes produced too many false positives (171 auto-merge, many wrong). A single hash algorithm can fluke a low distance on structurally similar but different houses. Requiring at least 2 of the 3 hashes to score ≤ threshold filters this noise, reducing to 116 auto-merge with ~89% precision.

**Finding 8 — agents list the same property with wildly different metadata across portals.**
Spot-checking the distinct tier revealed confirmed matches hiding there:
- BRK 3 ↔ P24 5905: same 5-bed house, KSh 55M vs 85M (~35% gap), different suburb names ("Near Braeburn School" vs "Lavington"), posted ~2 months apart.
- BRK 202 ↔ P24 3930: same house, listed as 8-bed KSh 250M on BRK vs 4-bed KSh 220M on P24.

These confirm that image matching catches real duplicates that field-based signals would never find — agents list the same house with different prices, bedroom counts, and suburb names across portals. These are inherently hard cases that the heuristic scorer cannot reliably catch without stronger image understanding (see Section 6).

---

## 6. Phase B: future improvements (conditional)

### 6a. Suburb adjacency map rework (immediate — next session)
The current adjacency map is reactively patched and has known gaps. Needs: (1) a name normalization step (strip "Off/Near/Along" prefixes, collapse "Rd"/"Road" etc.) before comparison, and (2) a geography-driven rebuild using local Nairobi knowledge rather than adding suburbs as they surface in errors. Expected to fix ~half of the current 13 false positives in auto-merge.

### 6b. SSIM verification (conditional — if review queue stays noisy after adjacency fix)
SSIM (Structural Similarity Index) compares two images at full resolution across brightness, contrast, and structure. Too expensive for blocking (~7M pairs), but viable as a **second-opinion scorer signal** on the ~100–200 candidate pairs that pass hash blocking. Would replace or supplement the hash distance signal in the scorer with something more accurate. Trigger: if review queue precision stays below 50% after the adjacency fix.

### 6c. Embedding-based matching (future — when same-house-different-photo cases emerge)
Neural image embeddings (e.g. CLIP) convert images to semantic vectors, enabling matching even when agents use completely different photos of the same property (different angle, different day). This is fundamentally beyond what any perceptual hash can do — hashing compares pixel patterns, embeddings compare meaning. Trade-offs: heavier dependencies (PyTorch/ONNX), more compute (~1–2 hours on a laptop for ~13,500 images), larger storage (512 floats per image vs 64 bits for a hash). Trigger: confirmed same-house-different-photo cases in the data (not yet observed). Worth mentioning in README as the designed upgrade path.

### 6d. Phase B: learned model (future — post-placement)
The hand-verified pairs from the Phase A spot-checking provide labeled training data. A small classifier (logistic regression or gradient-boosted tree) could be trained on the signal vector to learn optimal weights and thresholds from data rather than hand-tuning. Only worth pursuing if the heuristic scorer's precision plateaus below an acceptable level and manual weight tuning stops yielding improvements. The heuristic scorer remains the baseline to beat.
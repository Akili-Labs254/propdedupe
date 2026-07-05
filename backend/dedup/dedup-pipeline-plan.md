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

---

## 2. Signal definitions
*(to be filled in as each signal is built and reasoned through)*

## 3. Weighting & tiers
*(to be filled in once signals exist and can be reasoned about together)*

## 4. Baseline validation
*(to be filled in once there's a scorer to validate)*

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

## 6. Phase B: model comparison (future, conditional)
*(trigger criteria and scope to be defined once Phase A has verified labels)*

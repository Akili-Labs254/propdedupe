# 0006: Rentals as a first-class category

**Status:** accepted (Phase 1, July 2026)

## Decision
Add `rent` alongside `sale` as a hard-partitioned category: separate
scrape URLs, partitioned dedup (sale and rent listings never match:
same building, different market offer), parallel URL space
(`/apartments-for-rent/{suburb}` mirrors `/houses-for-sale/{suburb}`),
monthly price display.

## Why (validated 2026-07-15, portal data)
- Supply: P24 Nairobi rentals = **148,139 listings** vs ~12k for sale;
  BRK apartments-for-rent = 4,292 vs ~1.5k sale houses.
- Duplication: P24 rentals page 1 showed the same apartment 2–4×
  **on one page**: rental agents blast-post; dedup is worth more here.
- Demand shape: renters search repeatedly (move every 1–2 years),
  matching the long-tail SEO machinery; rental scam pain makes
  "verified, deduplicated, still-available" resonate hardest.
- Parsers needed zero changes for rental markup (smoke-tested);
  rent prices confirmed sane (10k–517k monthly).

## Wedge + capacity constraints
Rent wedge = **apartments/flats only**, and the P24 rental scrape is
**capped** (`refresh --rent-max-pages 500`, ~10k listings). Free-tier
Supabase is 500MB; measured ~7.5KB/listing means the full corpus
(~375MB+) doesn't fit. Raise the cap / upgrade tier when the traffic
gate justifies it. Known caveat: repeated capped runs churn
stale/active on listings beyond the cap as the site reorders pages.

## Data hygiene found along the way
Sale corpus contained price=0 rows (violating the POA-is-NULL rule) and
a KSh 95,000,000,000 outlier polluting segment stats. Cleaned; sanity
cap applied. Generic stock images (same hash across ≥5 distinct
properties: auction signs etc.) now auto-hidden after each rebuild
(`hide_generic_images()`).

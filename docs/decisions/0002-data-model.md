# 0002: Data model

**Status:** accepted (Phase 0)

## Decision
Three core tables: `sources`, `listings`, `properties`. See
`backend/db/schema.sql`.

- **listings**: one row per scraped advert. May be a duplicate.
- **properties**: one row per real-world property (the deduplicated entity).
- A listing's `property_id` is **NULL until the dedup engine matches it**.

## Deferred (named now, built later)
- `listing_images`: perceptual hash per image.
- `matches`: records *why* two listings were judged identical (auditable dedup).
- `scrape_runs`: provenance for each scrape.

## Why this is the spine
"Many noisy listings → few canonical properties" is the entire problem in one
schema. Designing it right early avoids painful migrations later, and the
listing / property split is the cleanest way to explain the project in one breath.

# 0003: Stable property identity across dedup rebuilds

**Status:** accepted (Phase 1, July 2026)

## Problem
`persist_dedup` clears and rebuilds `properties` on every run, so row IDs
change. Anything public that references a property (SEO URLs, indexed
pages, manual review decisions) broke on every refresh:

- `/property/9648` 404'd after the next rebuild (Google punishes this).
- Manual merge/dismiss decisions were stored as tier flips on `matches`
  rows, which the rebuild deletes: human review work was silently erased.

## Decision
Identity flows from **listings, not property rows**. Listings are never
deleted (only status-flipped) and `(source_id, source_listing_id)` never
changes, so anything that must survive a rebuild is keyed to listings.

1. **Slugs** (`backend/dedup/slugs.py`): before clearing, snapshot
   {listing → its property's slug}. Rebuilt properties inherit the
   majority slug of their members; new ones generate a deterministic
   slug (title + location + hash of the smallest member key). Slugs
   nobody inherited become slug→slug 301 redirects (`property_slugs`),
   which chain and survive further rebuilds.
2. **Review decisions** (`review_decisions` table): merge/dismiss is
   recorded against the listing-ID pair. Every rebuild reapplies them:
   dismissed pairs are never auto-merged regardless of score; merged
   pairs always group, even if blocking stops surfacing them.
3. **Manual property merges** (`/review/merge-properties`) store a
   representative listing pair, so they too re-merge on every rebuild,
   and the removed property's slug 301s to the kept one.

## Consequence
The rebuild-everything pipeline stays (simple, re-runnable) while public
URLs and human judgment are durable. If rebuild cost ever becomes the
bottleneck, incremental dedup can replace it without touching this
identity layer.

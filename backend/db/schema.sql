-- PropDeDupe: core schema (Phase 0)
-- Postgres (via Supabase). Three tables model the entire problem:
--   sources    : the websites we scrape
--   listings   : every individual advert we scrape (may be a duplicate)
--   properties : one row per REAL-WORLD property (the deduplicated entity)
--
-- The dedup engine's job: group listings that describe the same property and
-- point each listing at its canonical `properties` row.

create table if not exists sources (
    id          bigint generated always as identity primary key,
    name        text not null unique,              -- 'Property24 Kenya'
    base_url    text not null,
    created_at  timestamptz not null default now()
);

create table if not exists properties (
    id               bigint generated always as identity primary key,
    canonical_title  text,
    canonical_price  numeric(14, 2),
    location         text,
    bedrooms         smallint,
    created_at       timestamptz not null default now()
);

create table if not exists listings (
    id                 bigint generated always as identity primary key,
    source_id          bigint not null references sources(id),
    source_listing_id  text   not null,            -- the site's own id (e.g. 'item-4014286')
    url                text,
    title              text,
    price              numeric(14, 2),
    location           text,
    bedrooms           smallint,
    raw_json           jsonb,                       -- full scraped payload, for reprocessing
    property_id        bigint references properties(id),  -- NULL until matched
    scraped_at         timestamptz not null default now(),

    -- a listing is unique per (source, the source's own id)
    unique (source_id, source_listing_id)
);

-- indexes for the matching / serving queries to come
create index if not exists idx_listings_property_id on listings(property_id);
create index if not exists idx_listings_price       on listings(price);
create index if not exists idx_listings_bedrooms    on listings(bedrooms);

-- ─────────────────────────────────────────────────────────────────────────
-- DEFERRED (designed now, built in later weeks: do NOT create these yet):
--
--   listing_images(id, listing_id -> listings, image_url, phash)
--       perceptual hash per image. On Property24, exact-duplicate listings
--       reuse an IDENTICAL image URL: a near-free first-pass match. Re-
--       uploaded or cross-source dupes need perceptual hashing (phash).
--
--   matches(id, listing_a -> listings, listing_b -> listings,
--           text_score, price_delta, image_distance, decided_at)
--       records WHY two listings were judged the same. Makes dedup auditable.
--
--   scrape_runs(id, source_id -> sources, started_at, finished_at,
--               listings_found, status)
--       provenance: when each scrape ran and what it pulled.
-- ─────────────────────────────────────────────────────────────────────────

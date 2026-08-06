-- Backfill: NULL implausible floor areas already in the corpus.
-- Run ONCE in the Supabase SQL Editor. Idempotent (re-running is a no-op).
--
-- Mirrors SIZE_BOUNDS in backend/db/save.py, which now applies these bounds
-- at save time. Keep the two in sync if either changes.
--   10 m² .. 2000 m²
--
-- Bounds set from observed tails across 18,081 active listings with a size:
--   <10 m²    418 rows, smallest 1 m² — not a dwelling
--   20-25 m²  189 rows — a real Nairobi bedsitter, so the floor sits below
--   500-1000  796 rows — real, a large Karen or Runda house
--   >2000 m²  175 rows, largest 542,331 m² (54 hectares) — plot sizes,
--             typos, or land listings mis-parsed as floor area
--
-- Affects ~593 listings (3.3% of those carrying a size). Junk becomes NULL,
-- the same treatment as an unknown size — the listing survives and still
-- links out; we simply stop publishing a figure we don't believe.
--
-- NOTE on cluster_key: it hashes size_sqm, so these rows' keys now describe
-- a size they no longer hold. save_listings recomputes the key on the next
-- scrape of each listing, which self-corrects every ACTIVE row. Stale rows
-- keep a slightly stale key — harmless, since intra-source cluster grouping
-- only considers active listings.
--
-- Run the dedup rebuild afterwards so properties.size_sqm picks up the change.

-- Preview first — run this on its own before the update:
--   select count(*) filter (where size_sqm < 10)   as too_small,
--          count(*) filter (where size_sqm > 2000) as too_large
--   from listings where size_sqm is not null;

update listings
set size_sqm = null
where size_sqm is not null
  and (size_sqm < 10 or size_sqm > 2000);

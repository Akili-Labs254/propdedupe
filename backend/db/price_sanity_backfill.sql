-- Backfill: NULL implausible prices already in the corpus.
-- Run ONCE in the Supabase SQL Editor. Idempotent (re-running is a no-op).
--
-- Mirrors PRICE_BOUNDS in backend/db/save.py, which now applies these
-- bounds at save time. Keep the two in sync if either changes.
--   rent: 3,000 .. 5,000,000
--   sale: 500,000 .. 20,000,000,000
--
-- Affects ~428 listings (0.5% of the corpus). Junk prices become NULL —
-- the same treatment as POA — so the listing survives and still links
-- out to its source; we simply stop publishing a number we don't believe.
--
-- NOTE on cluster_key: it hashes the price, so these rows' keys now
-- describe a price they no longer hold. save_listings recomputes the key
-- on the next scrape of each listing, which self-corrects every ACTIVE
-- row. Stale/delisted rows keep a slightly stale key — harmless, since
-- intra-source cluster grouping only considers active listings.
--
-- Run the dedup rebuild afterwards so properties.price / price_min /
-- price_max pick up the change.

-- Preview first — run this on its own before the updates:
--   select category,
--          count(*) filter (where category='rent'
--                             and (price < 3000 or price > 5000000)) as rent_junk,
--          count(*) filter (where category='sale'
--                             and (price < 500000 or price > 20000000000)) as sale_junk
--   from listings where price is not null group by category;

update listings
set price = null
where price is not null
  and category = 'rent'
  and (price < 3000 or price > 5000000);

update listings
set price = null
where price is not null
  and category = 'sale'
  and (price < 500000 or price > 20000000000);

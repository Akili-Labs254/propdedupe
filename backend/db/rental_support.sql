-- Rentals as a first-class category. Run ONCE in the Supabase SQL
-- Editor BEFORE the first rental scrape. Idempotent: safe to re-run.
--
-- category is a HARD PARTITION in dedup: a house listed for sale AND
-- rent shares photos, but those are different market offers and must
-- never cross-match. (Rent prices are monthly; sale prices are totals.)

-- ── 1. listings.category ─────────────────────────────────────────────
-- Column already exists in the live schema; make its state explicit.
alter table listings add column if not exists category text;
update listings set category = 'sale' where category is null;
do $$ begin
    alter table listings
        add constraint listings_category_check
        check (category in ('sale', 'rent'));
exception when duplicate_object then null;
end $$;
create index if not exists idx_listings_category on listings(category);

-- ── 2. properties.category ───────────────────────────────────────────
alter table properties add column if not exists category text;
update properties set category = 'sale' where category is null;
create index if not exists idx_properties_category
    on properties(category, suburb, status);

-- ── 3. suburb_stats view: per category ───────────────────────────────
drop view if exists suburb_stats;
create view suburb_stats as
select
    suburb,
    category,
    bedrooms,
    grouping(bedrooms) = 1              as is_suburb_total,
    count(*)                            as property_count,
    min(price)                          as min_price,
    max(price)                          as max_price,
    percentile_cont(0.5) within group (order by price)
        filter (where price is not null) as median_price
from properties
where status = 'active' and suburb is not null
group by grouping sets ((suburb, category), (suburb, category, bedrooms));

grant select on suburb_stats to service_role;

-- SEO layer support: canonical suburb per property + aggregate stats view.
-- Run ONCE in the Supabase SQL Editor (safe to re-run), then:
--     python -m scripts.backfill_suburbs
-- (suburb normalization lives in Python — adjacency.py — so the backfill
-- is a script, not SQL. persist_dedup writes suburb itself from now on.)

-- ── 1. suburb column ─────────────────────────────────────────────────
alter table properties add column if not exists suburb text;
create index if not exists idx_properties_suburb
    on properties(suburb, status);

-- ── 2. segment stats view ────────────────────────────────────────────
-- One row per (suburb) and per (suburb, bedrooms) via GROUPING SETS.
-- is_suburb_total = true on the suburb-wide rows. Price stats skip
-- NULL prices (POA); property_count includes them.
create or replace view suburb_stats as
select
    suburb,
    bedrooms,
    grouping(bedrooms) = 1              as is_suburb_total,
    count(*)                            as property_count,
    min(price)                          as min_price,
    max(price)                          as max_price,
    percentile_cont(0.5) within group (order by price)
        filter (where price is not null) as median_price
from properties
where status = 'active' and suburb is not null
group by grouping sets ((suburb), (suburb, bedrooms));

grant select on suburb_stats to service_role;

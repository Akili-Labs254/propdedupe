-- Admin stats views + generic-image hiding.
-- Run ONCE in the Supabase SQL Editor. Idempotent: safe to re-run.

-- ── 1. Dedup rate per category (the "how bad are the dupes" number) ──
create or replace view dedup_stats as
select
    coalesce(l.category, 'unknown')      as category,
    l.status,
    count(*)                             as listings,
    count(distinct l.property_id)        as properties,
    round(100.0 * (count(*) - count(distinct l.property_id))
          / greatest(count(*), 1), 1)    as dup_pct
from listings l
where l.property_id is not null
group by 1, 2;

grant select on dedup_stats to service_role;

-- ── 2. Most-duplicated properties (worst offenders, with links) ──────
create or replace view most_duplicated as
select
    p.id, p.slug, p.title, p.category, p.suburb, p.price,
    count(l.id)                          as listing_count,
    count(distinct l.source_id)          as source_cnt
from properties p
join listings l on l.property_id = p.id
group by p.id, p.slug, p.title, p.category, p.suburb, p.price;

grant select on most_duplicated to service_role;

-- ── 3. Generic (stock) image hiding ──────────────────────────────────
-- An image hash appearing across MANY distinct properties is a stock
-- placeholder (auction signs, "land for sale" boards), not a photo of
-- the property. Real duplicate photos collapse into 1-2 properties;
-- placeholders span dozens. Clears image_url on affected properties so
-- cards render text-only (templates already handle missing images).
-- Called automatically by refresh.py after each rebuild.
create or replace function hide_generic_images(min_properties int default 5)
returns int language sql as $$
    with generic as (
        select l.image_phash
        from listings l
        where l.image_phash is not null and l.image_phash <> ''
          and l.property_id is not null
        group by l.image_phash
        having count(distinct l.property_id) >= min_properties
    ),
    cleared as (
        update properties p
        set image_url = null
        where p.image_url is not null
          and exists (
              select 1
              from listings l
              join generic g on g.image_phash = l.image_phash
              where l.image_url = p.image_url
          )
        returning 1
    )
    select count(*)::int from cleared;
$$;

grant execute on function hide_generic_images(int) to service_role;

-- ── 4. matches.category (filter the review queue by rent/sale) ───────
-- Both listings in a pair share a category (dedup partition guarantee),
-- so the pair's category = listing_a's category.
alter table matches add column if not exists category text;
update matches m
set category = l.category
from listings l
where l.id = m.listing_a_id and m.category is null;
create index if not exists idx_matches_category on matches(category);

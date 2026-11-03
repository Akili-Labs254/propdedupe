-- Bed/bath range per property (sources sometimes disagree: a DSQ /
-- domestic-staff room gets counted or not, so "4 bed" vs "5 bed" for the
-- same house, and showing the range is aggregator-only information for
-- buyers). Display only: search still filters on the property's primary
-- bedrooms/bathrooms, exactly like price_min/max vs price.
-- Run ONCE in the Supabase SQL Editor AFTER the current rebuild finishes.
-- Idempotent: safe to re-run. persist_dedup writes these columns itself
-- from now on; the UPDATE backfills the current build.

alter table properties add column if not exists beds_min  integer;
alter table properties add column if not exists beds_max  integer;
alter table properties add column if not exists baths_min numeric(4, 1);
alter table properties add column if not exists baths_max numeric(4, 1);

update properties p
set beds_min  = sub.bmn,
    beds_max  = sub.bmx,
    baths_min = sub.tmn,
    baths_max = sub.tmx
from (
    -- min()/max() skip NULLs, so a property whose members disagree on
    -- whether a field is filled still gets the range of the ones that are.
    select property_id,
           min(bedrooms) as bmn, max(bedrooms) as bmx,
           min(baths)    as tmn, max(baths)    as tmx
    from listings
    where property_id is not null
    group by property_id
) sub
where sub.property_id = p.id;

grant select, insert, update on properties to service_role;

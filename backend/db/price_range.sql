-- Price range per property (sources sometimes disagree on price —
-- showing the range is aggregator-only information for buyers).
-- Run ONCE in the Supabase SQL Editor AFTER the current rebuild
-- finishes. Idempotent — safe to re-run. persist_dedup writes these
-- columns itself from now on; the UPDATE backfills the current build.

alter table properties add column if not exists price_min numeric(14, 2);
alter table properties add column if not exists price_max numeric(14, 2);

update properties p
set price_min = sub.mn,
    price_max = sub.mx
from (
    select property_id, min(price) as mn, max(price) as mx
    from listings
    where price is not null and property_id is not null
    group by property_id
) sub
where sub.property_id = p.id;

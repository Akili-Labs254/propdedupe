-- Crawl-date stamp on properties: first_seen = when the earliest member
-- listing was first scraped. Powers "newest properties" sorting for both
-- admin checks and the consumer homepage.
-- Run ONCE in the Supabase SQL Editor. Idempotent — safe to re-run.
-- persist_dedup writes the column itself from now on; the UPDATE
-- backfills the current build.

alter table properties add column if not exists first_seen timestamptz;

update properties p
set first_seen = sub.fs
from (
    select property_id, min(first_seen) as fs
    from listings
    where property_id is not null
    group by property_id
) sub
where sub.property_id = p.id;

create index if not exists idx_properties_first_seen
    on properties(first_seen desc);

-- Partial index for the incremental hash backfill (scripts/refresh.py
-- stage_hashes). That query asks for "image_url set, image_phash null,
-- order by id" 100 rows at a time. Without this index Postgres walks the
-- primary key from the lowest id every batch; on 4 Nov 2026, with ~45k
-- fresh unhashed rows behind ~90k hashed ones, it hit the gateway timeout
-- (504 "upstream request timeout") 5,000 rows in.
--
-- Stays tiny: only rows still waiting for a hash are in it.
create index if not exists idx_listings_unhashed
  on public.listings (id)
  where image_phash is null and image_url is not null;

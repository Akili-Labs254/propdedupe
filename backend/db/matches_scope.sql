-- Distinguish cross-source matches from intra-source ones (same site,
-- re-uploaded image, different listing id — cluster_key misses these
-- because the image URL differs).
-- Run ONCE in the Supabase SQL Editor. Idempotent — safe to re-run.

alter table matches add column if not exists scope text not null default 'cross';
create index if not exists idx_matches_scope on matches(scope);

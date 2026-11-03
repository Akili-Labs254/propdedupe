-- Crash-safe slug snapshot. Run ONCE in the Supabase SQL Editor.
--
-- persist_dedup snapshots {listing → slug} in memory, then CLEARS the
-- properties table. A crash between clear and rebuild used to destroy
-- the snapshot (this happened 2026-07-16): slug inheritance was lost
-- and slugs regenerated deterministically. Now the snapshot is also
-- written here before clearing, and a rerun that finds no slugs in
-- properties recovers the snapshot from this table instead.

create table if not exists slug_snapshot (
    listing_id  bigint primary key,
    slug        text not null,
    saved_at    timestamptz not null default now()
);
alter table slug_snapshot enable row level security;
grant select, insert, update, delete on slug_snapshot to service_role;

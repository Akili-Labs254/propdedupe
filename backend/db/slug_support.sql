-- Stable property identity (slugs) + bulk linking.
-- Run ONCE in the Supabase SQL Editor before the next persist_dedup run.
-- Idempotent — safe to re-run.

-- ── 1. properties: slug (stable public URL) + status ────────────────
-- slug survives dedup rebuilds via reconciliation in persist_dedup:
-- a rebuilt property inherits the slug its member listings carried.
-- status: 'active' if any member listing is active, else 'inactive'
-- (consumer pages only serve active).

alter table properties add column if not exists slug text;
create unique index if not exists idx_properties_slug on properties(slug);

alter table properties add column if not exists status text not null default 'active';
create index if not exists idx_properties_status on properties(status);

-- ── 2. property_slugs: retired-slug redirects ────────────────────────
-- When dedup regrouping changes a property's canonical slug, the old
-- slug 301-redirects to the new one (slug → slug, NOT slug → id: ids
-- die on every rebuild, canonical slugs don't). Resolution may chain
-- (A → B → C); the web layer follows up to 5 hops.
-- No FK to properties on purpose — this table must outlive rebuilds.

create table if not exists property_slugs (
    slug        text primary key,
    target_slug text not null,
    updated_at  timestamptz not null default now()
);
alter table property_slugs enable row level security;
grant select, insert, update, delete on property_slugs to service_role;

-- ── 3. bulk listing→property linking ─────────────────────────────────
-- One RPC call links a whole batch (vs one REST round-trip per listing,
-- which made singleton linking take ~an hour and eat the HTTP/2 cap).

create or replace function link_listings(pairs jsonb) returns void
language sql as $$
    update listings l
    set property_id = (p.elem->>'property_id')::bigint
    from jsonb_array_elements(pairs) as p(elem)
    where l.id = (p.elem->>'listing_id')::bigint;
$$;

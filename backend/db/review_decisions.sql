-- Durable manual review decisions. Run ONCE in the Supabase SQL Editor.
--
-- Problem this fixes: merge/dismiss decisions were stored as tier flips
-- on `matches` rows — which persist_dedup DELETES on every rebuild, so
-- human review work was erased by the next refresh run. Listing IDs are
-- stable (listings are never deleted, only status-flipped), so decisions
-- keyed by listing pair survive rebuilds and get reapplied each run.

create table if not exists review_decisions (
    listing_a_id  bigint not null references listings(id),
    listing_b_id  bigint not null references listings(id),
    decision      text not null check (decision in ('merge', 'dismiss')),
    decided_at    timestamptz not null default now(),
    primary key (listing_a_id, listing_b_id),
    check (listing_a_id < listing_b_id)
);
alter table review_decisions enable row level security;
grant select, insert, update, delete on review_decisions to service_role;

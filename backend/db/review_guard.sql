-- Reject cross-category merge decisions at the source.
-- Run ONCE in the Supabase SQL Editor. Idempotent.
--
-- Why: review_decisions rows are durable and reapplied on every rebuild,
-- and persist_dedup unions merge decisions even when the pair were never
-- blocking candidates. That path bypasses the category partition, the
-- location gate and the beds gate at once, so one misclick in the batch
-- review UI permanently welds a rental onto a for-sale property.
-- persist_dedup now skips these defensively; this stops them being
-- recorded in the first place.

create or replace function reject_cross_category_merge()
returns trigger
language plpgsql
as $$
declare
    cat_a text;
    cat_b text;
begin
    if new.decision <> 'merge' then
        return new;
    end if;

    select category into cat_a from listings where id = new.listing_a_id;
    select category into cat_b from listings where id = new.listing_b_id;

    if cat_a is not null and cat_b is not null and cat_a <> cat_b then
        raise exception
            'cross-category merge rejected: listing % (%) + listing % (%)',
            new.listing_a_id, cat_a, new.listing_b_id, cat_b;
    end if;

    return new;
end;
$$;

drop trigger if exists trg_reject_cross_category_merge on review_decisions;
create trigger trg_reject_cross_category_merge
    before insert or update on review_decisions
    for each row execute function reject_cross_category_merge();

-- Refresh-pipeline support. Run ONCE in the Supabase SQL Editor before
-- the first scripts/refresh.py run. Idempotent: safe to re-run.

-- ── 1. status: backfill + default + guard ───────────────────────────
-- Rows scraped before status handling existed have status NULL.
-- Everything currently in the DB was seen at initial scrape → 'active';
-- the first refresh run will demote what has since disappeared.

update listings set status = 'active' where status is null;

alter table listings alter column status set default 'active';

do $$ begin
    alter table listings
        add constraint listings_status_check
        check (status in ('active', 'stale', 'delisted'));
exception when duplicate_object then null;
end $$;

-- ── 2. price_history: capture every price change via trigger ────────
-- Trigger (not app code) so ANY writer (scraper upsert, manual fix,
-- future admin UI) records history. Fires on insert (first price) and
-- on any update that changes price.

create or replace function record_price_change()
returns trigger language plpgsql as $$
begin
    if (tg_op = 'INSERT' and new.price is not null)
       or (tg_op = 'UPDATE' and new.price is distinct from old.price) then
        insert into price_history (listing_id, price, price_currency, recorded_at)
        values (new.id, new.price, new.price_currency, now());
    end if;
    return new;
end;
$$;

drop trigger if exists trg_price_history on listings;
create trigger trg_price_history
    after insert or update of price on listings
    for each row execute function record_price_change();

-- ── 3. price_history baseline ────────────────────────────────────────
-- The trigger only records prices from now on. Seed current prices as
-- the baseline so the first change has something to be a change FROM.
-- Idempotent: skips listings that already have history.

insert into price_history (listing_id, price, price_currency, recorded_at)
select l.id, l.price, l.price_currency, now()
from listings l
where l.price is not null
  and not exists (select 1 from price_history ph where ph.listing_id = l.id);

-- ── 4. sanity checks (run after, expect no NULL statuses) ───────────
-- select status, count(*) from listings group by status;
-- select count(*) from price_history;

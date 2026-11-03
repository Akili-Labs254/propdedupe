-- Invalidate image hashes when a listing's cover photo changes.
--
-- Bug this fixes (found during the 2026-07-15 intra-pair audit): the
-- scraper upsert overwrites image_url when a site changes a listing's
-- cover photo, but the stored perceptual hashes still describe the OLD
-- photo, so match rows can show "img 0.0" next to two visibly
-- different pictures. Nulling the hashes on URL change makes the next
-- refresh run re-hash them (its incremental stage picks up NULL phash).
--
-- Run ONCE in the Supabase SQL Editor. Idempotent: safe to re-run.

create or replace function invalidate_stale_hashes()
returns trigger language plpgsql as $$
begin
    if new.image_url is distinct from old.image_url then
        new.image_phash := null;
        new.image_ahash := null;
        new.image_whash := null;
    end if;
    return new;
end;
$$;

drop trigger if exists trg_hash_invalidation on listings;
create trigger trg_hash_invalidation
    before update of image_url on listings
    for each row execute function invalidate_stale_hashes();

-- One-off repair for rows already carrying stale hashes: we can't know
-- which ones drifted, so re-verify the cheap way: clear hashes for
-- listings whose last_seen is recent but whose hash was computed in the
-- original backfill era. OPTIONAL and heavier (re-hashes ~2 min/1000):
-- uncomment only if img-0.0-but-different-photos pairs keep appearing.
-- update listings set image_phash = null, image_ahash = null,
--     image_whash = null where status = 'active';

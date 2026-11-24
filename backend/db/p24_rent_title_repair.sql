-- One-off repair: give P24 rentals their real titles (24 Nov 2026).
--
-- Until today the P24 parser took the tile's first meta[itemprop="name"].
-- Rent tiles have no schema.org/Product block, only the agency's
-- RealEstateAgent one, so that meta was the AGENCY: of 46,968 active
-- rentals ~11,300 were titled "Rashford Properties" and the like, and
-- ~35,400 had no title, which the site rendered as "None".
--
-- The parser now reads the tile link's title attribute. This applies the
-- same rule to rows already stored, from the tile HTML kept in raw_json,
-- including rentals past the 1,500-page cap that no scrape will revisit.
-- Checked before writing: all 54,987 P24 rent rows have a link title.
--
-- Safe to re-run (only touches rows whose title differs). Doesn't touch
-- image_url or price, so neither the hash nor the price-history trigger
-- fires. Takes effect on the site after the next rebuild.
--
-- If the editor times out, run it four times with the extra line
--   and id % 4 = 0      (then 1, 2, 3)
-- added to the WHERE clause.

update listings
set title = substring(raw_json #>> '{}' from '<a href="/[^"]*" title="([^"]*)"')
where source_id = 2
  and category = 'rent'
  and substring(raw_json #>> '{}' from '<a href="/[^"]*" title="([^"]*)"') is not null
  and title is distinct from
      substring(raw_json #>> '{}' from '<a href="/[^"]*" title="([^"]*)"');

-- Check: expect 0.
select count(*) as rent_rows_still_without_a_link_title
from listings
where source_id = 2 and category = 'rent' and status = 'active'
  and title is distinct from
      substring(raw_json #>> '{}' from '<a href="/[^"]*" title="([^"]*)"');

-- Replace raw min/max price on segment pages with a robust 5th–95th
-- percentile band. Run ONCE in the Supabase SQL Editor. Idempotent.
--
-- Why: ~1.4% of priced rentals are junk from the source portals, agent
-- typos at both tails (a KSh 2 "monthly rent", a KSh 110,000,000 one).
-- Medians are robust and were always correct, but min() and max() are BY
-- DEFINITION the two most extreme values in the group, so 1.4% bad data
-- set 100% of the range lines. Every segment page: i.e. every SEO
-- landing page Google indexes: read "from KSh 2 to KSh 110,000,000".
--
-- percentile_disc (not _cont) returns an ACTUAL price from the set rather
-- than an interpolated one, so both ends of the band correspond to a real
-- listing someone can go and look at.
--
-- Columns are renamed min_price/max_price -> low_price/high_price on
-- purpose: they are no longer minima and maxima, and a column that lies
-- about what it holds is how the next bug gets written.
--
-- Residual: for segments near MIN_SEGMENT (5 properties) the 5th
-- percentile is still effectively the minimum, so a tiny segment
-- containing a junk price will still show it. Accepted: those are
-- low-traffic pages and the alternative is hardcoding per-category price
-- floors that then need maintaining.

drop view if exists suburb_stats;
create view suburb_stats as
select
    suburb,
    category,
    bedrooms,
    grouping(bedrooms) = 1              as is_suburb_total,
    count(*)                            as property_count,
    percentile_disc(0.05) within group (order by price)
        filter (where price is not null) as low_price,
    percentile_disc(0.95) within group (order by price)
        filter (where price is not null) as high_price,
    percentile_cont(0.5) within group (order by price)
        filter (where price is not null) as median_price
from properties
where status = 'active' and suburb is not null
group by grouping sets ((suburb, category), (suburb, category, bedrooms));

grant select on suburb_stats to service_role;

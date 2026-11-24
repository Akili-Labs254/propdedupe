# 0011: A scrape may not erase what it fails to read

**Status:** accepted (product track, November 2026)
**Relates to:** 0005 (cluster_key), 0008 (staleness requires a completed crawl)

## What happened

On 24 Nov 2026 Property24 changed the cover photo in each listing tile from
an SVG-style `<image>` element to a plain `<img>`. The parser selected only
`<image>`, found nothing, and returned every listing without a photo.

Nothing failed. Every listing still parsed, prices were fine, and the run
looked healthy. `save_listings` upserts every column, so it wrote NULL over
the stored image URL of every listing the crawl touched: **26,008 listings**
(9,273 sale, 16,735 rent). The hash-invalidation trigger then cleared their
perceptual hashes, because the image URL had "changed". It surfaced only
because the hash stage reported "0 hashed" for 4,777 new listings.

It was the second time. In August the price moved out of an attribute and
**~37,000 prices** went NULL over eleven days the same way.

The rebuild would have made it much worse. `cluster_key` hashes image URL,
beds, baths, size and price; with no image it fell back to
`"|2|2.0||85000.00"`, which every 2-bed, 2-bath KSh 85,000 flat in Nairobi
shares. Step 2 of the rebuild groups on that key, so the next rebuild would
have merged **21,909 listings into 2,992 false properties**, the largest
being 155 unrelated rentals, and taken their photos off the site. Nothing in
the rebuild checked its inputs before clearing the live tables.

The review that followed found a third case of the same shape, live on the
site: rent tiles carry only the agency's schema.org block, so "the first
`itemprop=name`" was the agency. Of 46,968 active rentals, ~35,400 had no
title and ~11,300 were titled with an agency's name, and rent property pages
rendered the word "None" as their heading and `<title>`.

The pattern in all three: **a selector stops matching, and the result is a
NULL, not an error.** A NULL that is then written over good data.

## Decision 1: fields a listing cannot lose are sticky

`url`, `title`, `location`, `address`, `image_url`, `bedrooms`, `baths`
(`STICKY_FIELDS` in `backend/db/save.py`). When a scrape comes back without
one and the stored row has it, the stored value is kept. An advert does not
lose its location or its bedroom count; when the scrape says it did, the
scrape is wrong.

Two fields are deliberately not sticky:

- **Price.** An advert can genuinely switch to "Price on application".
- **Size.** The parser returns no size on purpose for plots ("55 acres") and
  impossible figures. Stickiness would preserve an old misparse forever.

## Decision 2: a page-wide collapse stops the run before anything is written

Two layers, because each sees something the other cannot:

| check | where | fires when |
|---|---|---|
| `check_field_coverage` | parser | a page of 5+ listings has no image at all, or no price at all |
| `check_field_collapse` | save | among listings already stored, at least 3 and at least 25% come back without a field they had (price, image, title, location, bedrooms, url) |

The parser check covers pages of brand-new listings, which have nothing
stored to compare against. The save check covers partial breaks (one tile
variant, one field) and every watched field, including price, which is not
sticky. Either raises `ParserOutOfDate` (`FieldCollapse` is a subclass): the
category stops, is reported as crashed, never counts as complete, so it is
never staled, and `refresh --all` skips the rebuild.

The thresholds are judgment, not measurement. A normal page loses a price to
"Price on application" zero or one times, so three or a quarter of the page
is well clear of ordinary churn. Revisit if it fires on a healthy page.

## Decision 3: no image, no exact-duplicate key

`compute_cluster_key` returns None when a listing has no image. The key
exists to catch the same advert posted again, and the shared image URL is
the evidence for that. Without it, the key is just a price band.

Before 24 Nov no P24 listing lacked an image, so this was latent. It is the
reason a parser break would have become 2,992 wrong properties.

## Decision 4: the rebuild checks its inputs before clearing

`persist_dedup` now counts active listings with no image, an image but no
hash, and no price, and exits before touching anything if more than 5%, 5%
or 10% of them have the problem (`PREFLIGHT_LIMITS`). On the night of the
incident that was 26,008 of 68,855 (38%) without an image; normal is 0%,
~0% and ~1%.

`--force` overrides it only when running `persist_dedup` directly.
`refresh` never passes its own `--force` through, so overriding a stale
guard can't also wave a broken rebuild past this check.

## Consequences

- A listing whose photo really is removed keeps showing the old one. The
  card hides a broken image (`onerror`), so the cost is a blank tile at worst.
- A genuine site-wide change on the source (say P24 stops showing images on
  result pages) stops every scrape until someone looks. Intended: that is a
  decision for a person, not for a parser.
- A partial price break below the threshold still writes NULLs.
  `price_history` records every one, which is how August was dated.
- Rows misparsed before the fix (22 sizes, the rent titles) are corrected
  only when re-scraped, or by the one-off `p24_rent_title_repair.sql`.
- The guards cover P24. BuyRentKenya is blocked (ADR 0010) and its parser
  has no equivalent page check.
- The rebuild still clears before it rebuilds. These checks make a bad
  rebuild less likely; build-then-swap would make a crash harmless, and is
  still not done.

## Alternatives rejected

**A database trigger that keeps the old value when the new one is NULL.**
Catches every writer, but also blocks the deliberate NULLs: the price and
size sanity backfills set junk values to NULL on purpose. It would need a
bypass flag, and the rule would live in two places. The application-level
merge is visible in one function and tested.

**Make price sticky too.** Would hide genuine "Price on application"
changes, and the collapse check already covers a mass loss.

**Only the parser-level page check.** Misses partial breaks and every field
except image and price. It is what was added first on 24 Nov, before the
review showed the title bug had the same shape.

## Also fixed in the same review

- Rent titles: the parser reads the tile link's `title` attribute; pages
  fall back to "1 Bedroom Apartment to Rent" rather than "None";
  `backend/db/p24_rent_title_repair.sql` fixes stored rows from `raw_json`.
- Resuming `--stale` requires `--categories` (ADR 0008, amendment of
  24 Nov).
- The hash stage leaves images that fail on a network error for the next
  run instead of marking them permanently unfetchable, and stops if the
  network looks down.
- Page fetches ride out network drops of under ~100 seconds; database calls
  retry for ~31 seconds instead of ~7.
- `?page=` past the end redirects to page 1 instead of a 500; `/stats`
  works again and reports the active figures.

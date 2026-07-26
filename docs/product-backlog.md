# Product backlog — shelved ideas

Ideas parked deliberately during Phase 1 launch (July 2026). Nothing
here blocks the validation gate; revisit after traffic data exists.
Rough order within each section = leverage per unit of effort.

## Done

- **Bedroom/bathroom ranges on merged properties** (shipped July 2026) —
  `beds_min/max`, `baths_min/max` on properties; cards + detail show
  "4–5 bed" when sources disagree, with a DSQ explainer. Display only;
  search still filters on the primary count. Mirrors price_min/max.

## Bugs

- **Broken multi-word "nearby suburbs" link** — the segment-link builder
  doesn't slugify spaces, so it renders `/apartments-for-rent/spring valley`
  (space, broken URL) instead of `spring-valley`. Fix: slugify space→hyphen
  in the nearby-suburbs link generation in `segments.py`.

## Search & filters

- **Baths filter + baths on cards** — data already exists (`baths`
  numeric, half-baths handled). Small: one query param + form control.
  (Baths now also display as a range on cards; this is the filter half.)
- **Amenity keywords (garden, pool, DSQ, borehole, furnished, gym,
  backup power…)** — agents rarely fill P24's structured fields but DO
  write amenities into descriptions. Extract keyword flags at scrape /
  post-process time from stored `raw_json` descriptions into a
  `listings.amenities` array column; filter + display as chips.
  Regex/keyword pass first; no ML needed. Medium effort, high search
  value — nobody else surfaces this reliably.
- **Rent price presets** ("under 30k / 30–60k / 60–100k") — renters
  think in bands, not free-text mins.

## Similarity & discovery

- **"Similar properties" on detail pages, v1** — no ML required: same
  category + same/adjacent suburb (adjacency graph!) + beds ±0 + price
  ±20%. One query, big engagement win, deepens internal linking (SEO).
- **Recommendation-lite** — user enters budget + beds + 1–2 suburbs →
  ranked results reusing the v1 similarity scoring across adjacent
  suburbs. The adjacency graph is the moat here: "you searched
  Kilimani, here's what your money gets next door in Kileleshwa."
- **Similarity v2 (embeddings)** — image + description embeddings for
  true "looks like this one" similarity; also strengthens dedup
  (catches the different-photos re-list class). Phase B alongside the
  learned matching model.

## Data quality & dedup

- **Adjacency map expansion** — more suburbs, more edges, sub-areas
  (e.g. Westlands sub-neighbourhoods); the prefix-fallback helps but
  the graph itself is hand-curated and thin outside the core suburbs.
- **Multi-image hashing** (`listing_images`, deferred since ADR 0002) —
  hash ALL photos per listing, match on any-overlap. Catches
  same-property-different-cover re-lists (the "Runda pair" class).
  Image URLs largely already in `raw_json`; needs detail-page scrape
  for full galleries.
- **Raise the intra auto-merge cut** — triage-band data (0.10–0.147
  clean over 8 audited pages) suggests headroom; decide from
  accumulated `review_decisions` precision per score band, per ADR 0005.
- **houses-to-rent category** — rent currently apartments-only (wedge);
  both sources have the category, scraper is one URL per source.

## Market data (aggregator-only content)

- **Price-drop tracking** — `price_history` already records every
  change; surface "price dropped" badges, and later alerts. Strong
  retention + share-worthy.
- **Days-on-market** — from `first_seen`; "listed 60+ days" is
  negotiation signal no portal shows.
- **Suburb price index** — median price / price-per-m² per suburb per
  month from the stats view; a public "Nairobi rent index" page is an
  SEO + shareability magnet and costs one view + one template.
- **Public market analytics page ("Trends")** — the index grown into a
  destination: per-suburb median trends over time, rent vs sale, most/
  least expensive suburbs, supply counts, month-over-month movement.
  Powered by monthly snapshots of suburb_stats (start snapshotting
  EARLY — the page's value is the time axis). Journalist/content bait.
- **Per-property price history** — timeline on the property page from
  price_history ("listed at 85k in June, now 76k"). Buyer-side
  negotiation gold; pairs with the price-drop badge.

## Retention

- **Saved searches / alerts** — email or WhatsApp "new 2-beds in
  Kilimani under 80k this week." Needs accounts-lite (email only) —
  the first feature that captures a returning audience.
- **WhatsApp share button on cards** — one `wa.me` link; shares are
  the primary growth loop.

## Platform & polish

- **Map view** — Leaflet + OSM (decision per vision doc: free tier
  fine for a wedge site). Needs geocoding coverage first
  (`latitude/longitude` columns exist, sparsely filled).
- **Admin key → session cookie** — key currently rides the URL
  (leak-hardened with noreferrer, but a cookie is proper).
- **Bilingual copy pass** — the brand leads Swahili; a light
  English/Swahili toggle for hero + labels could deepen local trust.

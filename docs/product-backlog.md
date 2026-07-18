# Product backlog — shelved ideas

Ideas parked deliberately during Phase 1 launch (July 2026). Nothing
here blocks the validation gate; revisit after traffic data exists.
Rough order within each section = leverage per unit of effort.

## Search & filters

- **Baths filter + baths on cards** — data already exists (`baths`
  numeric, half-baths handled). Small: one query param + form control.
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

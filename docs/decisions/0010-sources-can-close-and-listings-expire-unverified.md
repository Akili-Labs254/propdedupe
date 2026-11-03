# 0010 — Sources can close, and unverified listings expire from display

**Status:** accepted (product track, September 2026)
**Relates to:** 0004 (stale detection), 0008 (staleness requires a completed crawl)

## What happened

BuyRentKenya re-platformed on 2026-09-01 and closed to automated access
within about sixteen hours of the migration.

| when | what |
|---|---|
| 1 Sep | New stack. JSON-LD `@type` became a list; `ItemList` moved out of `WebPage.mainEntity`; listing data left the structured data entirely; images moved from `i.roamcdn.net` to a Cloudflare Workers CDN. |
| 29 Aug – 1 Sep | Every scheduled CI run scraped **zero listings**. The parser returned `[]` silently and the crawl reported `complete=True`. |
| 2 Sep 00:06 | Rewritten parser worked: 225 listings, IDs matching existing rows. |
| 2 Sep ~16:50 | 403 on every request, from GitHub runners **and** the residential IP. |
| 6 Sep | Still 403. Site loads normally in a browser. |

The block is not IP-based and not User-Agent-based — `requests` with a
browser UA is refused while Safari from the same address is served. It is
client fingerprinting (TLS handshake, HTTP/2 behaviour, header order), which
identifies any HTTP library regardless of what it claims to be.

Their robots.txt was rewritten in the same migration and now caps pagination
at page 10, so the previous crawl pattern (600 pages sale, 1,500 rent) is
outside what they permit independently of the 403.

## Decision 1 — we do not circumvent it

Defeating browser fingerprinting requires tooling built for that purpose.
That is circumvention of a measure deliberately deployed, not a grey area.

We decline on grounds that are practical as much as principled. This project
argues that the market is full of actors who behave badly because it works —
bait listings left up to generate calls, duplicates left in to inflate
inventory. That argument is worth less coming from someone who forges
browser fingerprints to get around a "no".

The remaining options are to ask BRK for access, or to do without.

## Decision 2 — a closed source does not make its listings dead

The 12,252 BRK listings in the database were live when last verified on
2 September. BRK's refusal to serve a crawler is evidence about BRK's bot
policy and **nothing whatsoever** about whether those properties are still
for sale.

Marking them `stale` or `delisted` would repeat the error ADR 0008 exists to
prevent, one level up: treating absence of checking as evidence of removal.
`status` records what the source told us. A source that won't talk to us has
told us nothing.

**So `status` is left untouched, and the expiry is applied at display time.**

## Decision 3 — the verification horizon

A listing we have not re-checked in **90 days** is withheld from listing
pages. It keeps its status, its row and its slug, and returns automatically
if the source reopens.

### Why 90, when the data says 60

P24 sale, over the 60-day observable window:

| status | listings | median observed life |
|---|---|---|
| active | 10,083 | 59.9 days — the whole window |
| delisted | 2,207 | 19.6 days |
| stale | 599 | 53.2 days |

**78% of P24 sale listings persisted for the entire window.** Nairobi sale
inventory is long-lived; it does not turn over in weeks. So an aggressive
expiry would discard listings that are, on the base rate, more likely live
than gone — destroying real coverage to fix a presentation problem.

The corpus begins 4 July, so 60 days is the longest lifetime we can observe
at all. Everything past it is censored. 90 days is therefore explicitly a
**judgment about how long "we last checked" stays credible**, not a
measurement — and it is set beyond the evidence deliberately, so that the
error is in the direction of keeping inventory rather than deleting it.

Re-derive this once the corpus is old enough to observe 120+ day lifetimes.

### What the buyer sees before then

Withholding is the last resort, not the mechanism. Before the horizon, the
answer is disclosure — the "Seen for N days" badge already carries the
truth, and cards should state when the listing was last verified rather than
implying every listing is equally fresh. Coverage now differs by source, and
the site should say so rather than averaging it into a single claim.

## Consequences

- **"Every advert here was still live when we last checked" survives**, but
  only because "when" is now shown rather than implied. Without the
  disclosure the claim becomes false for 23% of the corpus.
- **Cross-source dedup degrades over time.** It produced 604 multi-source
  properties on 6 Sep. As BRK ages out, that falls toward zero and the
  engine's most distinctive capability has nothing to work on.
- **Two sources was always fragile.** Either could do this; one did, with
  sixteen hours' notice and no warning. A third and fourth source is now a
  structural requirement rather than a growth idea.
- **The measured results still stand.** The engine was built, measured at ~96%
  auto-merge precision, and ran across two sources on real data. A source
  changing its terms afterwards does not undo that.

## Also fixed during this incident

- `parse()` returning `[]` was indistinguishable from an empty page, so a
  dead parser read as end-of-pagination. Now raises `ParserOutOfDate`.
- `is_end_of_pagination` treated all 4xx as end-of-list, disagreeing with
  ADR 0008's own table. A 403 streak reported `complete=True` on zero
  listings. Now `frozenset({404, 410})` — refusal is not absence.
- BRK location parsing leaked headline text; suburb normalisation then split
  every area in two (`kilimani` vs `kilimani, nairobi`), halving cross-source
  merges and reducing the Kilimani 1-bed page from 437 properties to 13.
- `MIN_SCRAPE_RATIO` was the last line of defence in **three separate
  incidents this week**. It keeps working, which is quietly making it
  load-bearing. That is worth watching.

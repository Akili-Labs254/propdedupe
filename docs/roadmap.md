# PropDeDupe — Week-by-Week Plan

**How to use this:** This is your *big map* — the stable, zoomed-out view of the whole
project. Don't track day-to-day tasks here. Keep a separate, short "this week" list
(3–5 items) that you scribble on and check off as you go. Glance at this map weekly to
confirm you're on track; live in the weekly list day-to-day.

**Timeline:** ~12 weeks, ~4–5 focused hours on the days you work (not every day).
Weeks 4, 8, and 12 are deliberate **catch-up buffers** — you *will* need them, and that's
planned, not failure.

Each week has: **Build** (what you make) · **Learn** (the new skill) · **Deliverable**
(proof it's done) · **Risk** (what tends to go wrong) · **Interview point** (what it lets
you say).

**Done:** Phase 0 — workshop set up, scraping proven possible.

---

### Week 1 — Real scraper: BuyRentKenya, one page
- **Build:** Grow the spike into a proper parser that pulls all ~22 listings off one
  BuyRentKenya page into clean Python objects.
- **Learn:** Python for real — functions, loops, lists/dictionaries, working with JSON, a
  small regex to pull "4 Bed" and "in Runda" out of the title.
- **Deliverable:** Run it, see all 22 listings printed with name, price, bedrooms, location.
- **Risk:** Missing or oddly-shaped fields; the title-parsing regex needs fiddling. Normal.
- **Interview point:** "Where possible I parse structured data (JSON-LD) instead of brittle
  HTML — it's more robust."

### Week 2 — Save to the database + all pages
- **Build:** Wire the scraper into Supabase so listings get stored, and loop over all 59
  pages instead of one.
- **Learn:** Connecting Python to Postgres, INSERT from code, using the unique constraint so
  re-running doesn't create duplicates, being polite across many pages (rate limiting).
  *This week plays to your SQL strength.*
- **Deliverable:** Run once → ~1,300 BuyRentKenya listings in your `listings` table. Run
  again → no duplicate rows.
- **Risk:** Connection/auth hiccups; handling the "this listing already exists" case.
- **Interview point:** "A small ETL pipeline — extract from the web, transform to a
  normalized shape, load into Postgres idempotently (safe to re-run)."

### Week 3 — Second source: Property24
- **Build:** A Property24 scraper using HTML selectors. The payoff: it produces the *same*
  listing shape, so it drops straight into your existing save-to-database code unchanged.
- **Learn:** BeautifulSoup selectors — finding elements, reading attributes (like price in
  `content="..."`), handling the beds/baths/parking order.
- **Deliverable:** Both sites scraping into the same table.
- **Risk:** Property24's markup has edge cases; some tiles miss fields.
- **Interview point:** "Pluggable source adapters — adding a new site is one new class
  fulfilling a shared interface, with zero pipeline changes."

### Week 4 — Catch-up buffer + first dedup signal
- **Build:** Breathing room to finish anything that slipped. Then start the dedup engine with
  the *cheapest* signal: flag listings that share an identical image URL (your Property24
  insight).
- **Learn:** pytest (replace the placeholder smoke test with a couple of real ones); SQL
  GROUP BY to find duplicates.
- **Deliverable:** A query/script that lists candidate duplicates by shared image.
- **Risk:** Discovering the data's messier than hoped — better now than later.
- **Interview point:** "I started with the cheapest high-precision signal before anything
  fancy — pragmatic over clever."

### Week 5 — Text similarity (rapidfuzz)
- **Build:** The matching brain, part one — compare listing titles/descriptions with fuzzy
  matching.
- **Learn:** rapidfuzz, similarity scores and thresholds, and the core idea of precision vs
  recall (too loose merges different homes; too strict misses real duplicates). Start filling
  the `matches` table that records *why* two listings matched.
- **Deliverable:** Given two listings, a similarity score; a first pass grouping likely-same
  ones.
- **Risk:** Threshold tuning; Kenyan naming/address variation.
- **Interview point:** "Fuzzy string matching with tunable thresholds — I reasoned about the
  precision/recall tradeoff explicitly."

### Week 6 — Combine signals into real matching
- **Build:** Put it together — same area + near-identical price + high text similarity → same
  property. Create a `properties` row and link the matched listings to it (set `property_id`).
- **Learn:** Combining several weak signals into one decision; normalizing locations; the
  logic that turns many listings into one canonical property.
- **Deliverable:** Run the matcher → `properties` fills up, listings get linked.
- **Risk:** The genuinely hard cases — similar-but-different homes, or the same home priced
  very differently by two agents.
- **Interview point (your flagship):** "Multi-signal entity resolution — I combine image,
  text, and structured signals, and every merge is auditable through a matches table."

### Week 7 — Perceptual image hashing (imagehash)
- **Build:** The advanced signal — catch re-uploaded or slightly-altered images that aren't
  byte-identical.
- **Learn:** Downloading images, perceptual hashing (phash), Hamming distance, and why it
  beats exact-matching for cross-site duplicates.
- **Deliverable:** Image-hash comparison feeding the matcher; catches duplicates the
  URL-match missed.
- **Risk:** Slow/failing image downloads; false matches on generic stock photos.
- **Interview point:** "Perceptual hashing for near-duplicate image detection — a genuinely
  interesting technique, and a strong signal where text and price are ambiguous."

### Week 8 — Catch-up buffer + measure it
- **Build:** More breathing room. Then evaluate how good the dedup actually is.
- **Learn:** Building a small hand-labeled test set and measuring accuracy (precision) against
  it.
- **Deliverable:** A short "how good is the dedup?" number you can quote.
- **Risk:** Finding weak spots — which is exactly the point of measuring.
- **Interview point:** "I evaluated my matcher against hand-labeled ground truth — I don't
  claim it works, I measured it." (Recruiters love this.)

### Week 9 — The API (FastAPI)
- **Build:** Expose the deduplicated results through a small web API.
- **Learn:** FastAPI basics — defining endpoints, returning JSON, the auto-generated
  interactive docs, querying the DB for one result per property.
- **Deliverable:** A running API; `/properties` returns the clean list; the auto-docs page
  works.
- **Risk:** FastAPI's learning curve; shaping the database query.
- **Interview point:** "A documented REST API (free interactive Swagger docs) over the
  deduplicated data."

### Week 10 — Thin frontend
- **Build:** A simple page that calls your API and shows the clean, duplicate-free listings.
  Show the value: "1,291 raw → ~900 unique."
- **Learn:** Minimal frontend — we'll decide plain HTML+fetch vs a tiny Next.js page when we
  get here (leaning simple, given time and priorities).
- **Deliverable:** A working page displaying deduplicated properties.
- **Risk:** Frontend rabbit holes — we timebox this hard.
- **Interview point:** "End-to-end: a working UI over my own API — with a concrete
  before/after impact number."

### Week 11 — Deploy + light CI
- **Build:** Put the API and frontend online.
- **Learn:** Deployment, production environment variables, and a basic GitHub Action that runs
  your tests on each push.
- **Deliverable:** A live URL you can send someone.
- **Risk:** Deployment config quirks.
- **Interview point:** "It's deployed and live — and tests run automatically on every commit."

### Week 12 — Catch-up buffer + package the story
- **Build:** Final polish. Tidy the README, clean the commit history, maybe record a short
  demo clip.
- **Learn:** Turning the project into a 2-minute spoken pitch.
- **Deliverable:** A portfolio-ready repo and a rehearsed story.
- **Interview point:** The whole narrative, packaged and confident.

---

## If you fall behind: scope cuts

The map has three buffer weeks, but if you're still behind, drop things in **this order** —
each cut still leaves a strong project:

1. **Frontend** → replace with a screenshot or a simple notebook showing results.
2. **Perceptual image hashing** → keep the exact image-URL match.
3. **Second source** → one site fully done proves the concept.
4. **Deployment** → a clean repo + README is enough; live demo is bonus.

**Never cut:** the scraper → database pipeline, and *some* dedup with a measured result.

**Minimum viable version that still impresses:** "I scrape a real Kenyan property site,
detect duplicate listings with image and text matching, and reduce N listings to M unique
properties — here's the code and the numbers." That alone is a strong spring-week story.
Everything else is upside.

---

## Your strongest interview talking points

Pulled together, these are what the project lets you claim:

1. **Entity resolution / record linkage** — your flagship. A real, named computer-science
   problem, not a toy.
2. **Multi-signal matching** with explicit precision/recall reasoning.
3. **Perceptual image hashing** — the technically impressive piece that makes you memorable.
4. **ETL into Postgres** with idempotent (safe-to-re-run) loading.
5. **Pluggable architecture** — new source = one new class.
6. **Measured, not claimed** — accuracy evaluated against ground truth.
7. **Full-stack** — documented API, frontend, deployed.

**Why this lands for fintech specifically:** entity resolution and fuzzy matching are exactly
what fintech does for transaction deduplication, identity/KYC matching, and fraud detection.
You're not just showing generic coding — you're solving the *same class of problem* they work
on daily. Say that in an interview.

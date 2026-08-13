# Code drills

The eight writing sessions cover "tell me about a hard bug." These cover
the other half: *walk me through this function*, *what happens if I pass
it X*, *what breaks if I delete this line*.

Do one drill set in place of a writing session when you want something
practical, or after the writing is done as revision.

**Answers are at the bottom.** Say your answer out loud first — writing it
lets you be vaguer than speech allows, and speech is the exam.

---

## Drill A — cold opens (5 min)

Pick a file at random. Two minutes, out loud: what does this do, why does
it exist, what would break without it. No reading ahead.

```
ls backend/**/*.py scripts/*.py | shuf -n 1
```

Not about perfect recall — about not freezing when a file you half
remember appears on a shared screen.

## Drill B — traces

Follow a concrete input all the way through. Say what changes in which
table, in order.

**B1.** A P24 rent page returns a listing that's already in `listings`,
unchanged except the price dropped from 85,000 to 75,000. Trace it to the
point a user sees the new price.

**B2.** A P24 *sale* page returns a listing whose `source_listing_id`
already exists with `category='rent'`. What happens, and why?

**B3.** Two BRK listings have the same image URL, same beds, same baths,
same size, same price. Which dedup path catches them — and what if the
price differs by 500,000?

**B4.** A listing is seen on Monday, missing Wednesday, missing Friday.
What's its status on Saturday, and what does that depend on?

**B5.** Someone opens `fikia.co.ke/20m` on their phone, filters to
Syokimau, opens a property, clicks through to BuyRentKenya. What gets
recorded, and where?

## Drill C — predict the break

For each: what breaks, how would you notice, and how bad is it?

**C1.** Remove `.order("id")` from a paginated fetch.
**C2.** Change `TIER_AUTO_MERGE` from 0.36 to 0.50.
**C3.** Delete the location gate (`signals["location"] >= 0.5`).
**C4.** Make `save_listings` overwrite `category` instead of refusing.
**C5.** Key slugs to `properties.id` instead of listings.
**C6.** Remove `@lru_cache` from `get_client`.
**C7.** Set `INTRA_AUTO_CUT` from 0.10 to 0.36 (matching cross-source).
**C8.** Have the scraper return `complete=True` when it hits the cap.

## Drill D — numbers you should have cold

Interviewers remember specifics. You have them; most candidates don't.

- Scorer weights, and which is largest
- The three tier thresholds
- `INTRA_AUTO_CUT`, and why it differs from cross-source
- Blocking distance intra vs cross, and why they differ
- Duplicate rate across the corpus
- What fraction of properties appear on more than one site
- Auto-merge precision
- The free-tier ceiling and current usage
- The API gateway timeout that shapes every bulk write

## Drill E — hostile questions

A sharp interviewer will attack the weakest part. Better to have met
these here first. None have a comfortable answer; all have an honest one.

**E1.** "Your cross-source dedup resolves about 1% of properties. Was the
hardest part of this worth building?"

**E2.** "These are asking prices, not sale prices. Doesn't that make the
analysis meaningless?"

**E3.** "Isn't this just scraping two websites?"

**E4.** "You have 14,000 pairs in a review queue nobody reviews. Is the
system actually working?"

**E5.** "Your properties table is rebuilt every run. Isn't that fragile?"

**E6.** "Why heuristics instead of a trained model?"

**E7.** "What's the weakest part of this system?"

---
---

# Answers

## B — traces

**B1.** Upsert on `(source_id, source_listing_id)` updates `price`, bumps
`last_seen`, resets `status='active'`. A Postgres **trigger** on
`listings` writes the old→new price into `price_history` — a trigger, not
app code, so *any* writer records history. The user sees it after the
next dedup rebuild refreshes `properties`, since the web layer reads
`properties`, not `listings`.

**B2.** Refused. `save.py` compares the stored category and skips the row
rather than overwriting. Category is a hard partition: flipping one would
corrupt `price_history` (a rent figure landing in a sale price series)
and let cross-category merges through. Expect exactly one per P24 sale
page — they slot a promoted rental into every one, 651 pages produced 651
skips.

**B3.** Identical on all five fields → same `cluster_key` (md5 of image
URL + beds + baths + size + price), caught by the exact-match path with
no scoring. Change the price and the key changes, so it falls through to
the perceptual-hash pass: same image, hashes match at distance 0–2, and
it's scored. Price dissimilarity is 25% of the weight, so a large gap can
push it past `INTRA_AUTO_CUT` (0.10) into review.

**B4.** Depends entirely on whether those crawls **completed**. If they
reached the end of pagination: stale after the first miss, and delisted
once `last_seen` is older than `DELIST_DAYS` (7) — so still stale on
Saturday. If either crawl stopped at its page cap, the stale pass is
skipped for that category and the listing stays active. That's ADR 0008.

**B5.** `/20m` 302s to `/?category=sale&max_price=20000000&sort=price_asc&ref=tt-20m`.
GoatCounter records the landing path including the ref; the ref is stashed
in `sessionStorage`. The filter and property page are ordinary pageviews.
The outbound click fires an event at `out/buyrentkenya/tt-20m` — source
plus campaign, because the ref is carried through the session.

## C — predict the break

**C1.** PostgREST gives no ordering guarantee without it, so `.range()`
windows can overlap or skip rows. You'd silently process some listings
twice and miss others. Nearly invisible — the run completes, the counts
are just wrong. This is why it's a standing rule.

**C2.** Auto-merge absorbs everything currently in review, so distinct
properties get welded together. Precision collapses from ~96%; users see
one listing where two different homes existed. Merges are the expensive
direction — a wrong merge hides a real property, a wrong split just shows
a duplicate.

**C3.** Pairs agreeing on image, price and beds but in genuinely
unrelated suburbs would auto-merge. The gate exists because image match
isn't conclusive — developments reuse renders across sites.

**C4.** A rental would flip to sale mid-series, corrupting `price_history`
and putting a monthly rent into a sale price distribution. Traced once
already: 764 churning listings, and rent-priced rows appearing at sale
scale.

**C5.** Every indexed URL points at a different property after each
rebuild — 200 with wrong content, not 404. Google keeps it indexed while
the content silently changes underneath. ADR 0003.

**C6.** Every web request builds a fresh Supabase client with its own
httpx pool and SSL context, none ever closed. They accumulate until the
512 MB Render box OOMs — three crashes on 7 Aug, ~7 hours apart,
accelerating with traffic.

**C7.** Intra-source pairs share a CDN and a listing template, so they
score much lower than cross-source pairs; 0.36 would auto-merge distinct
units in the same development. 0.10 was measured, not guessed (ADR 0005).

**C8.** Exactly the 12 Aug bug: 7,240 live listings marked stale or
delisted, 41% of everything then flagged gone.

## D — numbers

Image .30, price .25, beds .20, location .20, baths .05 — image largest,
baths smallest and deliberately so (portals report baths inconsistently).
Auto-merge < 0.36, review ≤ 0.50, distinct above. `INTRA_AUTO_CUT` 0.10.
Blocking ≤ 4 intra, ≤ 8 cross. ~41% of intake is duplicate. ~1% of
properties span more than one site. ~96% auto-merge precision. 500 MB
free tier, ~302 MB used. 60-second API gateway timeout.

## E — hostile questions

**E1.** Concede the number immediately, then separate the engine from the
corpus. Cross-source dedup resolves ~1% *because coverage is two sources
and one dominates* — it's ahead of the corpus, not wrong. The value users
get today is intra-source: 41% of intake removed. If asked what you'd do
differently: grow coverage before sophistication.

**E2.** Agree, then narrow the claim. These are asking prices from live
adverts and nobody paid them. That's fine for "what does the market ask
for a 4-bed in Syokimau" and useless for "what do houses sell for."
Naming the limit of your own data is the answer.

**E3.** The scraping is the easy part and took a week. The problem is
that 41% of what you collect is the same property twice, and the
interesting bit is deciding *which* records refer to one home when no
shared identifier exists and location strings disagree. That's entity
resolution.

**E4.** Be straight: the queue grows faster than it's triaged, and a
large slice of it is one unresolved product question — whether a
development's individually-listed units are one property with a price
range or many. Also say why you haven't triaged: review decisions are
durable and reapplied every rebuild, so triaging before deciding
permanently encodes a guess. That's judgement, not neglect.

**E5.** Rebuilding is deliberate — it makes the pipeline reproducible
from listings, which is the only durable data. The fragility is real and
handled: slugs key to listings, retired slugs 301, `slug_snapshot` is
crash-guarded after an incident that halved it.

**E6.** A heuristic you can explain beats a model you can't, at this
scale — you can point at any merge and name the signal that caused it. A
learned model is Phase B, compared against this as a baseline. You also
have no labelled ground truth beyond manual review, which is the actual
blocker.

**E7.** Pick one and be specific. Best answers: no tests on `refresh.py`
until it had already cost 7,240 listings; `source_count` counting dead
listings so the sitemap's multi-source filter selects on an inflated
signal; or the development-merging question still undecided. Having a
real one ready is worth more than everything else going well.

# System tour — follow one listing end to end

Read this **with the files open**, in this order. It is the spine of the
whole project: what happens to a single advert from the moment it exists
on Property24 to the moment someone clicks through to it from Fikia.

Ten stops, each with the file to open and one question. If you can answer
the question, move on. If you can't, that stop is where your gap is —
note it and keep going rather than stalling.

Budget an hour. Don't take notes yet; the writing sessions come after.

> The good news, before you start: your own comments are the best
> documentation here. Most stops explain themselves once the file is open.

---

## 1. A scraper fetches a page

**`backend/scraper/base.py`** (75 lines) → **`property24.py`**, **`buyrentkenya.py`**

Every source parses different HTML — P24 is BeautifulSoup over markup, BRK
reads JSON-LD — but both must emit the same `ScrapedListing` dataclass.
That common shape is what lets everything downstream ignore which site a
listing came from.

*Q: Why does the pipeline never need to know the source of a listing?*

## 2. It gets saved

**`backend/db/save.py`** (163 lines)

Upsert on `(source_id, source_listing_id)`, which bumps `last_seen` and
resets `status='active'`. Three guards live here: price bounds, size
bounds, and the category-conflict refusal.

*Q: Why is `price = NULL` for POA rather than 0? And why must the Python
and SQL `cluster_key` formulas be byte-for-byte identical?*

## 3. Its image gets hashed

**`scripts/backfill_all_hashes.py`**

Three perceptual hashes per image — phash, ahash, whash. Not one, because
any single hash has failure modes the others don't share.

*Q: What is a perceptual hash actually measuring, and why is it not the
same as comparing image URLs?*

## 4. Same-site duplicates get found

**`scripts/intra_source_match.py`** (91 lines)

Two mechanisms. `cluster_key` catches exact re-posts — identical image
URL plus specs plus price. This pass catches the harder case: the same
photo re-uploaded under a new URL, where the hashes match but the URLs
don't. Threshold is 4 here, tighter than cross-source.

*Q: Why is the intra-source threshold tighter than the cross-source one?*
(The answer is in the docstring and it's a genuinely good one.)

## 5. Cross-site candidates get blocked

**`scripts/cross_source_match.py`** (139 lines)

Triple-hash blocking: `min(phash, ahash, whash)` Hamming distance ≤ 8,
requiring 2-of-3 agreement. This is a *candidate generator*, not a
decision — it narrows millions of possible pairs to a scoreable few.

*Q: Why block first instead of scoring every possible pair? What's the
cost if the threshold is too tight, versus too loose?*

## 6. Candidates get scored

**`backend/dedup/scorer.py`** (104 lines) + **`adjacency.py`** (479 lines)

Five signals, weighted: image .30, price .25, beds .20, baths .05,
location .20. Each outputs *dissimilarity* — 0 identical, 1 different.
Output is three tiers: auto-merge, review, distinct.

The adjacency map has two layers: name normalisation (strips "Off",
"Near", "Along", maps aliases) and a physical border graph, so P24's
"Lavington" and BRK's "Kilimani" score ~0.3 rather than 1.0.

*Q: Why is no single field allowed to veto a match? Why is location a
score rather than a blocking key?* — these two are the heart of the
project and the most likely interview questions in it.

## 7. Groups become properties

**`scripts/persist_dedup.py`** (the big one)

Union-find turns pairs into groups, then three steps write them:
cross-source groups, cluster groups, then singletons. Every listing ends
up with a `property_id`. The whole properties table is rebuilt each run.

*Q: If properties are rebuilt every run, how do URLs stay stable?*
(→ stop 8.)

## 8. Identity survives the rebuild

**`backend/dedup/slugs.py`**, ADR 0003

Slugs key to **listings**, not properties, because listings are the only
stable identity in the system. Retired slugs 301 to their replacement.
`slug_snapshot` is the crash-safe copy, guarded by `SNAPSHOT_SHRINK_GUARD`.

*Q: What breaks if slugs key to properties instead?*

## 9. Dead listings get removed

**`scripts/refresh.py::stage_stale`**, ADR 0008

Active but not seen this run → stale. Stale for `DELIST_DAYS` → delisted.
Three guards, and the one you added today: a category whose crawl stopped
at the page cap is skipped entirely.

*Q: Why is "not seen this run" only evidence of removal when the crawl
completed?* — this is session 1's story, so you'll write it properly after.

## 10. Someone searches, and clicks out

**`backend/web/routes.py`**, **`segments.py`**, **`vanity.py`**

Server-rendered Jinja. Segment pages are the SEO surface (`MIN_SEGMENT`,
thin pages `noindex` not 404). Vanity codes carry their own attribution
because TikTok strips referrers. Outbound clicks are the conversion event.

*Q: Why serve thin pages with noindex instead of 404-ing them? Why is the
outbound click the closest thing this site has to a conversion?*

---

## After the tour

Go back to `docs/reconciliation-plan.md` and start session 1. It'll be a
different exercise now — you'll be writing to consolidate something you've
just re-read, rather than trying to retrieve it cold.

If more than three of the ten questions were blank, do the tour twice
before writing anything. That's not a setback; it's the measurement the
whole exercise exists to take.

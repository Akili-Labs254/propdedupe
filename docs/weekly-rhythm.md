# Weekly rhythm

A repeating cycle, so the week doesn't get decided by whatever seems most
interesting at 10pm. Roughly 3–4 hrs on a working day.

**The one rule:** reconciliation happens first, before the editor opens.
It's the only item with a real deadline and the only one that loses every
time it's scheduled last. Everything else on this page can slip a day.

---

## Every working day

| | | |
|---|---|---|
| **1** | Reconciliation session | 45 min |
| **2** | TikTok post | 20 min |
| **3** | Project slot | 60–90 min |

**Reconciliation** — one session from `docs/reconciliation-plan.md`,
written as prose. Eight of them, then a file-opening pass. Done before
September.

**TikTok** — verify the numbers against production *today*, then cards,
voiceover, post. This has caught three real bugs, which is most of its
value. If a claim doesn't survive verification, don't film it — that has
already happened twice and both times the data was right and the plan
was wrong.

**Project slot** — whatever the project actually needs today. Most weeks
that is *not* building:

- verifying a deploy, or checking a run that finished overnight
- admin: Search Console, GoatCounter settings, Render, the domain
- planning: next week's focus, the next video batch, the ads test
- GTM: property groups, outreach, writing something up
- backlog triage — deleting items that no longer matter counts as work
- and when nothing above is pending, the week's build item

The point of naming it a *slot* rather than "build" is that admin
silently expands to fill a build slot and then feels like failure. It
isn't. Half of what has moved this project forward — the vanity codes,
the probe script, the ads prep — was admin or planning.

---

## Monday

- Check the weekend's CI runs (BRK, light daily + full Mon/Wed/Fri)
- Pick **one** focus for the week and **one** video theme. The focus is
  usually a build item, but "get the ads test live" or "find and read
  three property groups" are equally valid weeks
- Write both down. Anything not on that list is next week's problem

## Wednesday

- **Manual P24 crawl** — can't run in CI (datacenter IPs blocked), needs
  a residential connection and several hours. Start it before bed:

  ```
  set -o pipefail
  LOG=logs/refresh-$(date +%Y%m%d-%H%M).log
  caffeinate -is python -u -m scripts.refresh --all --sources p24 \
      --rent-max-pages 1500 2>&1 | tee "$LOG"
  echo "exit=$? finished $(date)" | tee -a "$LOG"
  ```

- Morning after: `grep -E "exit=|SKIPPING|complete=|→ stale" logs/refresh-*.log`

## Friday

- Search Console: indexed page count, coverage errors, top queries
- Check DB size against the 500 MB tier (currently ~302 MB)

## Weekend — one sitting, ~1 hr

The only time analytics get read. At ~15 visits/day the daily noise
exceeds the signal, and checking it daily has been actively demoralising
for no informational gain.

- Visits for the week, **excluding `/feedback/*`** (not your traffic)
- **Outbound clicks** — the number that matters. As a share of sessions
- Any `?ref=` tags — did a campaign produce anything
- Google referrals — is SEO compounding yet
- Decide next week's build item from what the numbers say, not from what
  seems interesting

---

## Monthly, or when triggered

- Re-run `scripts/probe_depth.py` before adding any scraper index
- Re-measure `INTRA_AUTO_CUT` per ADR 0005 — overdue, queue is 14,205
- Prune delisted rows if disk gets tight
- Re-read `docs/product-backlog.md` and delete what no longer matters

---

## Standing guardrails

Things learned the expensive way. Each one cost something.

- **Don't read analytics daily.** Weekly, in one sitting.
- **Don't triage the review queue** until the development-merging
  question is decided (backlog). Review decisions are durable and
  reapplied on every rebuild — triaging now permanently encodes an
  answer to a question you haven't settled. Three wrong merges from
  19 July came from exactly this.
- **Don't add a scraper index** without checking `probe_depth` first. An
  index that can't be crawled to the end stops its whole category
  staling (ADR 0008).
- **Don't film a claim you haven't re-verified today.** The corpus moves
  under you — video 3's numbers were void within four days.
- **Don't start a second build item** before the first is finished.
- **Don't let the project slot eat the reconciliation slot.** It will
  try. It's more interesting and the feedback is faster. September
  doesn't move.

---

## What "a good week" looks like

Not "shipped a lot." Four or five reconciliation sessions written, four
videos posted, one build item finished, one hour of honest numbers at the
weekend. That pace finishes the reconciliation with three weeks to spare
and still moves the product forward.

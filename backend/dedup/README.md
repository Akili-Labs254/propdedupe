# Deduplication engine (planned)

The heart of the project. Decides which `listings` describe the same real-world
`property`, then links them. Built in stages, cheapest signal first:

1. **Exact image-URL match** — on Property24, duplicate listings reuse the
   identical image URL. Near-free, catches the obvious dupes.
2. **Text similarity** (`rapidfuzz`) — fuzzy-match titles / descriptions.
3. **Price + location proximity** — same area, near-identical price.
4. **Perceptual image hashing** (`imagehash`) — catches re-uploaded or
   cross-site images that aren't byte-identical.

Each match will be recorded in a `matches` table with its scores, so every dedup
decision is explainable. (Empty for now — implemented from ~Week 3.)

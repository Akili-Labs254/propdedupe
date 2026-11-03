# Deduplication engine

The core of PropDeDupe. Decides which `listings` describe the same real-world
`property`, then links them.

Works in two layers:

1. **Intra-source (P24):** `cluster_key` exact match on `image_url + bedrooms
   + baths + size_sqm + price`. Collapses ~36% of P24's listings as redundant.

2. **Cross-source (BRK ↔ P24):** triple-hash blocking (phash + ahash + whash,
   2-of-3 agreement at threshold ≤ 8), then weighted scoring on image distance,
   price, bedrooms, baths, and suburb adjacency. Outputs three tiers:
   auto-merge, review queue, distinct.

Key files:
- `scorer.py`: weighted evidence scorer (5 signals, location gate)
- `adjacency.py`: suburb name normalization + adjacency graph (78 suburbs, 100 edges)

See `dedup-pipeline-plan.md` for full signal definitions, weight reasoning,
and the iteration log.

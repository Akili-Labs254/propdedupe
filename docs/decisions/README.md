# Decision records

Each file records one decision: the problem, what was decided, and why.
Later records amend earlier ones rather than editing them, so read them in
order if you want the full history. (0004 was accidentally used twice, hence
0004a and 0004b.)

| # | Decision |
|---|---|
| [0001](0001-architecture-and-stack.md) | Architecture and stack |
| [0002](0002-data-model.md) | Data model: listings vs properties |
| [0003](0003-stable-property-identity.md) | Keep property URLs stable across rebuilds |
| [0004a](0004a-intra-source-dedup-policy.md) | Same-site dedup thresholds, set by measured precision |
| [0004b](0004b-refresh-stale-detection-durable-decisions.md) | Refresh pipeline and stale detection |
| [0005](0005-intra-source-dedup-measured-threshold.md) | Same-site auto-merge threshold from a hand audit |
| [0006](0006-rentals-category-expansion.md) | Rentals as a separate category |
| [0007](0007-overrides-may-not-cross-invariants.md) | Manual overrides can't break invariants |
| [0008](0008-staleness-requires-a-completed-crawl.md) | Only a completed crawl can mark listings stale |
| [0009](0009-development-listings-collapse-to-one-property.md) | A development's identical units show as one property |
| [0010](0010-sources-can-close-and-listings-expire-unverified.md) | When a source blocks us: don't circumvent, expire listings gradually |

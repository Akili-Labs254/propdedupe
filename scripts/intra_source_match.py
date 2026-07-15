"""Intra-source candidate matching: same site, same image content,
different listing rows.

cluster_key catches exact re-posts (identical image URL + specs +
price). This pass catches the re-upload case: same photo re-encoded
under a NEW URL — perceptual hashes match, URLs don't.

Threshold is TIGHTER than cross-source (4 vs 8): within one source the
same CDN pipeline served both copies, so true re-uploads sit at
distance 0-2. Cross-source needs 8 to tolerate different portals'
re-compression and watermarks; intra-source doesn't.

Run standalone for a distance histogram:
    python -m scripts.intra_source_match
"""
from typing import Dict, List, Tuple

from backend.db.client import get_client
from scripts.cross_source_match import fetch_distinct_hashes, hamming_distance

INTRA_THRESHOLD = 4


def find_intra_matches(
    hash_map: Dict[Tuple, List[int]],
    threshold: int = INTRA_THRESHOLD,
) -> List[Tuple[int, List[int], List[int]]]:
    """All-pairs comparison within one source's distinct hash triples.

    Returns [(min_distance, ids_a, ids_b), ...] where 2 of 3 hashes
    agree at <= threshold. Also emits identical-triple groups (several
    listings sharing the exact same image content under different URLs)
    as distance-0 candidates.

    Hex strings are converted to ints ONCE up front — this loop is
    O(n²) and P24 has ~10k+ distinct triples (~50M+ comparisons).
    """
    candidates = []

    items = []
    for triple, ids in hash_map.items():
        ints = tuple(int(h, 16) for h in triple)
        items.append((ints, ids))
        # Identical image content under different listing rows
        if len(ids) > 1:
            candidates.append((0, [ids[0]], ids[1:]))

    total = len(items)
    for i in range(total):
        ints_a, ids_a = items[i]
        for j in range(i + 1, total):
            ints_b, ids_b = items[j]
            d0 = hamming_distance(ints_a[0], ints_b[0])
            d1 = hamming_distance(ints_a[1], ints_b[1])
            d2 = hamming_distance(ints_a[2], ints_b[2])
            below = (d0 <= threshold) + (d1 <= threshold) + (d2 <= threshold)
            if below >= 2:
                candidates.append((min(d0, d1, d2), ids_a, ids_b))
        if (i + 1) % 500 == 0:
            print(f"  Compared {i + 1}/{total} triples...")

    return candidates


def main():
    db = get_client()
    for sid, name in ((1, "BRK"), (2, "P24")):
        print(f"\nFetching {name} hashes...")
        hashes = fetch_distinct_hashes(db, source_id=sid)
        print(f"  {len(hashes)} distinct triples "
              f"(~{len(hashes) * (len(hashes) - 1) // 2:,} comparisons)")
        cands = find_intra_matches(hashes)

        by_dist: Dict[int, int] = {}
        for dist, _, _ in cands:
            by_dist[dist] = by_dist.get(dist, 0) + 1
        print(f"  {len(cands)} candidate pairs — by min distance:")
        for d in sorted(by_dist):
            print(f"    distance {d}: {by_dist[d]}")


if __name__ == "__main__":
    main()

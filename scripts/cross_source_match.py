"""
Cross-source candidate matching using triple-hash blocking.
min(phash, ahash, whash) Hamming distance <= threshold.
"""
from typing import Dict, List, Tuple
from backend.db.client import get_client


def hex_to_int(hex_str: str) -> int:
    return int(hex_str, 16)


def hamming_distance(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def fetch_distinct_hashes(db, source_id: int,
                          category: str = None) -> Dict[str, List[int]]:
    """
    Return {(phash, ahash, whash): [listing IDs]} for one source.
    Only includes rows where all three hashes are populated.

    category partitions the match space: sale and rent listings of the
    same building share photos but are different market offers and must
    never be candidates against each other.
    """
    hash_map: Dict[str, List[int]] = {}
    start = 0
    batch_size = 1000

    while True:
        query = (
            db.table("listings")
            .select("id, image_phash, image_ahash, image_whash")
            .eq("source_id", source_id)
        )
        if category:
            query = query.eq("category", category)
        result = (
            query
            .not_.is_("image_phash", "null")
            .not_.is_("image_ahash", "null")
            .not_.is_("image_whash", "null")
            .order("id")
            .range(start, start + batch_size - 1)
            .execute()
        )
        if not result.data:
            break
        for row in result.data:
            key = (row["image_phash"], row["image_ahash"], row["image_whash"])
            hash_map.setdefault(key, []).append(row["id"])
        start += batch_size

    return hash_map


def triple_hash_distance(
    hashes_a: Tuple[str, str, str],
    hashes_b: Tuple[str, str, str],
    threshold: int = 8,
) -> Tuple[int, bool]:
    """
    Compute Hamming distances for phash, ahash, whash.
    Returns (min_distance, passes_blocking).
    passes_blocking is True only if at least 2 of 3 hashes
    score at or below the threshold.
    """
    distances = []
    for ha, hb in zip(hashes_a, hashes_b):
        distances.append(hamming_distance(hex_to_int(ha), hex_to_int(hb)))
    below = sum(1 for d in distances if d <= threshold)
    return min(distances), below >= 2

def find_cross_matches(
    brk_hashes: Dict[Tuple, List[int]],
    p24_hashes: Dict[Tuple, List[int]],
    threshold: int = 8,
) -> List[Tuple[Tuple, Tuple, int, List[int], List[int]]]:
    """
    Brute-force compare every BRK hash triple against every P24 hash triple.
    Returns matches where min(phash, ahash, whash) <= threshold.
    """
    matches = []
    brk_items = list(brk_hashes.items())
    p24_items = list(p24_hashes.items())

    total = len(brk_items)
    for i, (brk_triple, brk_ids) in enumerate(brk_items, 1):
        for p24_triple, p24_ids in p24_items:
            dist, passes = triple_hash_distance(brk_triple, p24_triple, threshold)
            if passes:
                matches.append((brk_triple, p24_triple, dist, brk_ids, p24_ids))

        if i % 100 == 0:
            print(f"  Compared {i}/{total} BRK hashes...")

    return matches


def main():
    db = get_client()

    print("Fetching BRK hashes...")
    brk_hashes = fetch_distinct_hashes(db, source_id=1)
    print(f"  {len(brk_hashes)} distinct BRK hash triples")

    print("Fetching P24 hashes...")
    p24_hashes = fetch_distinct_hashes(db, source_id=2)
    print(f"  {len(p24_hashes)} distinct P24 hash triples")

    print(f"\nComparing {len(brk_hashes)} x {len(p24_hashes)} = "
          f"~{len(brk_hashes) * len(p24_hashes):,} pairs...")

    matches = find_cross_matches(brk_hashes, p24_hashes, threshold=8)

    print(f"\n=== RESULTS ===")
    print(f"Total candidate matches (min distance <= 8): {len(matches)}")

    by_dist: Dict[int, int] = {}
    for _, _, dist, _, _ in matches:
        by_dist[dist] = by_dist.get(dist, 0) + 1

    print("\nMatches by min Hamming distance:")
    for d in sorted(by_dist):
        print(f"  distance {d}: {by_dist[d]} pairs")

    matches.sort(key=lambda x: x[2])
    print(f"\nTop 20 closest matches:")
    for brk_triple, p24_triple, dist, brk_ids, p24_ids in matches[:20]:
        print(f"  dist={dist}  BRK ids={brk_ids}  P24 ids={p24_ids}")


if __name__ == "__main__":
    main()
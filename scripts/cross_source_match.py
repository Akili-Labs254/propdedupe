"""
Compare perceptual hashes across BRK and P24 to find
cross-source candidate duplicates via Hamming distance.
"""

from typing import Dict, List, Tuple
from backend.db.client import get_client


def hex_to_int(hex_str: str) -> int:
    """Convert a hex-encoded pHash string to an integer for bitwise ops."""
    return int(hex_str, 16)


def hamming_distance(a: int, b: int) -> int:
    """Count differing bits between two integers (XOR then popcount)."""
    return bin(a ^ b).count("1")


def fetch_distinct_hashes(db, source_id: int) -> Dict[str, List[int]]:
    """
    Return {phash_hex: [list of listing IDs sharing that hash]}
    for one source. Paginated with .order("id").
    """
    hash_map: Dict[str, List[int]] = {}
    start = 0
    batch_size = 1000

    while True:
        result = (
            db.table("listings")
            .select("id, image_phash")
            .eq("source_id", source_id)
            .not_.is_("image_phash", "null")
            .order("id")
            .range(start, start + batch_size - 1)
            .execute()
        )
        if not result.data:
            break
        for row in result.data:
            hash_map.setdefault(row["image_phash"], []).append(row["id"])
        start += batch_size

    return hash_map


def find_cross_matches(
    brk_hashes: Dict[str, List[int]],
    p24_hashes: Dict[str, List[int]],
    threshold: int = 10,
) -> List[Tuple[str, str, int, List[int], List[int]]]:
    """
    Brute-force compare every BRK hash against every P24 hash.
    Returns matches: (brk_hash, p24_hash, distance, brk_ids, p24_ids)
    """
    matches = []
    brk_items = [(h, hex_to_int(h), ids) for h, ids in brk_hashes.items()]
    p24_items = [(h, hex_to_int(h), ids) for h, ids in p24_hashes.items()]

    total = len(brk_items)
    for i, (brk_hex, brk_int, brk_ids) in enumerate(brk_items, 1):
        for p24_hex, p24_int, p24_ids in p24_items:
            dist = hamming_distance(brk_int, p24_int)
            if dist <= threshold:
                matches.append((brk_hex, p24_hex, dist, brk_ids, p24_ids))

        if i % 100 == 0:
            print(f"  Compared {i}/{total} BRK hashes...")

    return matches


def main():
    db = get_client()

    print("Fetching BRK hashes...")
    brk_hashes = fetch_distinct_hashes(db, source_id=1)
    print(f"  {len(brk_hashes)} distinct BRK hashes")

    print("Fetching P24 hashes...")
    p24_hashes = fetch_distinct_hashes(db, source_id=2)
    print(f"  {len(p24_hashes)} distinct P24 hashes")

    print(f"\nComparing {len(brk_hashes)} x {len(p24_hashes)} = "
          f"~{len(brk_hashes) * len(p24_hashes):,} pairs...")

    matches = find_cross_matches(brk_hashes, p24_hashes, threshold=10)

    print(f"\n=== RESULTS ===")
    print(f"Total candidate matches (distance <= 10): {len(matches)}")

    # Break down by distance for analysis
    by_dist: Dict[int, int] = {}
    for _, _, dist, _, _ in matches:
        by_dist[dist] = by_dist.get(dist, 0) + 1

    print("\nMatches by Hamming distance:")
    for d in sorted(by_dist):
        print(f"  distance {d}: {by_dist[d]} pairs")

    # Show the closest matches in detail
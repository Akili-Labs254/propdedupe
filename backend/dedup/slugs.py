"""Stable property slugs across dedup rebuilds.

The problem: persist_dedup wipes and rebuilds `properties`, so row IDs
change every run, but public URLs must never break. The listings layer
has stable identity ((source_id, source_listing_id) never changes), so
identity flows from members: a rebuilt property inherits the slug that
the majority of its member listings carried before the rebuild.

Usage (persist_dedup):
    alloc = SlugAllocator(snapshot)        # {listing_id: old_slug}
    slug = alloc.slug_for(member_rows)     # inherit or generate
    retired = alloc.compute_retired(listing_to_new_pid)
                                           # {old_slug: new_property_id}
"""
import hashlib
import re
from collections import Counter, defaultdict
from typing import Dict, List

MAX_BASE_LEN = 70


def slugify(text: str) -> str:
    """'5 Bed Villa in Karen' → '5-bed-villa-in-karen'."""
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:MAX_BASE_LEN].rstrip("-") or "property"


class SlugAllocator:
    """Assigns each rebuilt property a slug, preferring inheritance.

    snapshot: {listing_id: slug its property had before this rebuild}.
    Empty on the first run: everything generates fresh.
    """

    def __init__(self, snapshot: Dict[int, str]):
        self._snapshot = snapshot
        self._claimed = set()

    def slug_for(self, member_rows: List[dict]) -> str:
        """Inherit the most common unclaimed old slug, else generate.

        member_rows need: id, title, location, source_id,
        source_listing_id. The generated suffix comes from the
        lexicographically smallest member key, so re-generating the
        same group yields the same slug (deterministic).
        """
        old = [self._snapshot.get(r["id"]) for r in member_rows]
        old = [s for s in old if s]
        for slug, _ in Counter(old).most_common():
            if slug not in self._claimed:
                self._claimed.add(slug)
                return slug

        rep = member_rows[0]
        base = slugify(f"{rep.get('title') or 'property'} "
                       f"{rep.get('location') or ''}")
        key = min((str(r.get("source_id")), str(r.get("source_listing_id")))
                  for r in member_rows)
        suffix = hashlib.md5(f"{key[0]}:{key[1]}".encode()).hexdigest()[:6]

        slug = f"{base}-{suffix}"
        n = 2
        while slug in self._claimed:
            slug = f"{base}-{suffix}-{n}"
            n += 1
        self._claimed.add(slug)
        return slug

    def compute_retired(self, listing_to_new_pid: Dict[int, int]) -> Dict[str, int]:
        """Old slugs nobody inherited → the new property_id that now
        holds the plurality of that slug's former member listings.
        The caller maps property_id → canonical slug for the redirect."""
        by_slug = defaultdict(list)
        for lid, slug in self._snapshot.items():
            by_slug[slug].append(lid)

        retired = {}
        for slug, lids in by_slug.items():
            if slug in self._claimed:
                continue  # still canonical somewhere
            counts = Counter(
                listing_to_new_pid[l] for l in lids if l in listing_to_new_pid
            )
            if counts:
                retired[slug] = counts.most_common(1)[0][0]
        return retired


if __name__ == "__main__":
    # Tiny self-test, mirrors adjacency.py's style
    print("slugify:")
    for raw, want in [
        ("5 Bed Villa with En Suite in Karen", "5-bed-villa-with-en-suite-in-karen"),
        ("  Häuser!! (Nairobi) ", "h-user-nairobi"),
        ("", "property"),
    ]:
        got = slugify(raw)
        print(f"  '{raw}' → '{got}' {'✓' if got == want else '✗ got ' + got}")

    print("inheritance:")
    alloc = SlugAllocator({1: "old-slug-abc", 2: "old-slug-abc"})
    rows = [{"id": 1, "title": "X", "location": "Y",
             "source_id": 1, "source_listing_id": "a"},
            {"id": 2, "title": "X", "location": "Y",
             "source_id": 2, "source_listing_id": "b"}]
    s1 = alloc.slug_for(rows)
    print(f"  inherited: {s1} {'✓' if s1 == 'old-slug-abc' else '✗'}")
    s2 = alloc.slug_for([{"id": 3, "title": "New House", "location": "Karen",
                          "source_id": 1, "source_listing_id": "c"}])
    print(f"  generated: {s2} {'✓' if s2.startswith('new-house-karen-') else '✗'}")

"""Sample development groups so the merge threshold can be measured
rather than guessed.

ADR 0009 sets MIN_DEVELOPMENT_GROUP = 4 on a plausibility argument: four
adverts identical on eight fields are unlikely to be four independent
flats, two might be. That argument deserves the same treatment the scorer
got: click through to the source adverts and count how often the group is
really one building.

Writes a markdown worksheet with a verdict column to fill in. Groups are
sampled per size band, because the risk is concentrated in the small ones.

    python -m scripts.audit_development_groups
    python -m scripts.audit_development_groups --sample 15 --seed 7

Read-only. Touches nothing.
"""

import argparse
import random
from collections import defaultdict
from typing import Dict, List, Optional

from backend.db.client import get_client
from backend.dedup.development import development_key, MIN_DEVELOPMENT_GROUP

BATCH_SIZE = 1000

# (label, min_size, max_size). Split either side of the threshold so the
# worksheet shows what the rule keeps apart as well as what it merges.
BANDS = [
    ("2 — merged only if threshold drops to 2", 2, 2),
    ("3 — merged only if threshold drops to 3", 3, 3),
    ("4-5 — merged at the current threshold", 4, 5),
    ("6-10 — merged", 6, 10),
    ("11+ — merged", 11, 10 ** 9),
]


def fetch_active_properties(db) -> List[dict]:
    rows: List[dict] = []
    offset = 0
    while True:
        # .order("id") before .range(): without a stable sort PostgREST may
        # repeat or skip rows across pages.
        batch = (db.table("properties")
                 .select("id, slug, category, suburb, location, title, "
                         "bedrooms, bathrooms, size_sqm, price, source_count")
                 .eq("status", "active")
                 .order("id")
                 .range(offset, offset + BATCH_SIZE - 1)
                 .execute())
        if not batch.data:
            break
        rows.extend(batch.data)
        if len(batch.data) < BATCH_SIZE:
            break
        offset += BATCH_SIZE
    return rows


def fetch_listings_for(db, property_ids: List[int]) -> Dict[int, List[dict]]:
    """Member adverts per property, chunked: a 200-id `in_` filter builds a
    URL long enough to be rejected."""
    out: Dict[int, List[dict]] = defaultdict(list)
    CHUNK = 50
    for i in range(0, len(property_ids), CHUNK):
        chunk = property_ids[i:i + CHUNK]
        batch = (db.table("listings")
                 .select("id, property_id, url, image_url, price, title, "
                         "status, source_id")
                 .in_("property_id", chunk)
                 .order("id")
                 .execute())
        for row in batch.data or []:
            out[row["property_id"]].append(row)
    return out


def group_properties(rows: List[dict]) -> Dict[str, List[dict]]:
    groups: Dict[str, List[dict]] = defaultdict(list)
    for row in rows:
        key = development_key(row)
        if key is not None:
            groups[key].append(row)
    return groups


def sample_bands(groups: Dict[str, List[dict]], per_band: int,
                 seed: Optional[int]) -> List[tuple]:
    rng = random.Random(seed)
    picked = []
    for label, lo, hi in BANDS:
        candidates = [(k, v) for k, v in groups.items() if lo <= len(v) <= hi]
        if not candidates:
            continue
        # Sort before sampling: dict order depends on insertion, so an
        # unsorted sample is not reproducible even with a fixed seed.
        candidates.sort(key=lambda kv: kv[0])
        chosen = rng.sample(candidates, min(per_band, len(candidates)))
        for key, members in chosen:
            picked.append((label, key, members))
    return picked


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=10,
                    help="groups to sample per size band (default 10)")
    ap.add_argument("--seed", type=int, default=None,
                    help="fix for a reproducible sample")
    ap.add_argument("--out", default="docs/development-group-audit.md")
    args = ap.parse_args()

    db = get_client(timeout=600)

    print("Fetching active properties...")
    rows = fetch_active_properties(db)
    print(f"  {len(rows)} active properties")

    groups = group_properties(rows)
    multi = {k: v for k, v in groups.items() if len(v) > 1}
    merged = {k: v for k, v in multi.items()
              if len(v) >= MIN_DEVELOPMENT_GROUP}
    collapse_all = sum(len(v) for v in multi.values()) - len(multi)
    collapse_thresh = sum(len(v) for v in merged.values()) - len(merged)

    print(f"  {len(multi)} groups >1, {len(merged)} at or above the "
          f"threshold of {MIN_DEVELOPMENT_GROUP}")
    print(f"  collapse if all merged: {collapse_all} rows")
    print(f"  collapse at threshold:  {collapse_thresh} rows")

    picked = sample_bands(multi, args.sample, args.seed)
    prop_ids = [p["id"] for _, _, members in picked for p in members]
    print(f"Fetching adverts for {len(prop_ids)} sampled properties...")
    listings = fetch_listings_for(db, prop_ids)

    lines = [
        "# Development group audit",
        "",
        "Generated by `python -m scripts.audit_development_groups`. "
        "Read-only sample; regenerate freely.",
        "",
        f"- active properties: **{len(rows)}**",
        f"- groups larger than one: **{len(multi)}** "
        f"(collapse: {collapse_all} rows)",
        f"- groups at or above the threshold of {MIN_DEVELOPMENT_GROUP}: "
        f"**{len(merged)}** (collapse: {collapse_thresh} rows)",
        "",
        "## How to score this",
        "",
        "For each group, open the adverts and decide: **is this one "
        "building?** Different units in one development count as *one* "
        "(that is the merge this rule is for). Two unrelated buildings that "
        "happen to match count as *false*.",
        "",
        "Write `one` / `false` / `unclear` in the verdict line. The number "
        "that matters is the false rate per band — if it is near zero at "
        "size 3, the threshold can drop; if it is material at 4-5, it "
        "should rise.",
        "",
    ]

    current_band = None
    for label, key, members in picked:
        if label != current_band:
            lines += ["", f"## Band {label}", ""]
            current_band = label
        member_ids = [p["id"] for p in members]
        adverts = [a for pid in member_ids for a in listings.get(pid, [])]
        rep = members[0]

        lines += [
            f"### {rep.get('title')} — {rep.get('suburb')} "
            f"({len(members)} properties, {len(adverts)} adverts)",
            "",
            f"- price `{rep.get('price')}`  beds `{rep.get('bedrooms')}`  "
            f"baths `{rep.get('bathrooms')}`  size `{rep.get('size_sqm')}`",
            f"- location `{rep.get('location')}`",
            f"- key `{key[:110]}`",
            "",
        ]
        # Cap the advert list: a 200-member group needs no more than a
        # handful of links to judge, and the worksheet has to stay readable.
        for advert in adverts[:8]:
            mark = "" if advert.get("status") == "active" else " *(inactive)*"
            lines.append(f"  - [{advert['url']}]({advert['url']}){mark}")
        if len(adverts) > 8:
            lines.append(f"  - _...and {len(adverts) - 8} more_")
        lines += ["", "**Verdict:** ", ""]

    with open(args.out, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"\nWrote {args.out} — {len(picked)} groups to review.")


if __name__ == "__main__":
    main()

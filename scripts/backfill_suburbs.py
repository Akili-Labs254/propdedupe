"""Backfill properties.suburb for the current build.

persist_dedup computes suburb at insert time from now on; this script
covers properties that already exist. Re-runnable: only touches rows
where suburb IS NULL.

Run: python -m scripts.backfill_suburbs
"""
from collections import defaultdict

from backend.db.client import get_client
from backend.dedup.adjacency import normalize_suburb


def main():
    db = get_client()

    rows = []
    offset = 0
    while True:
        batch = (db.table("properties")
                 .select("id, location")
                 .is_("suburb", "null")
                 .not_.is_("location", "null")
                 .order("id")
                 .range(offset, offset + 999)
                 .execute())
        if not batch.data:
            break
        rows.extend(batch.data)
        if len(batch.data) < 1000:
            break
        offset += 1000

    print(f"{len(rows)} properties need a suburb")

    by_suburb = defaultdict(list)
    for row in rows:
        suburb = normalize_suburb(row["location"])
        if suburb:
            by_suburb[suburb].append(row["id"])

    done = 0
    for suburb, ids in by_suburb.items():
        for i in range(0, len(ids), 200):
            chunk = ids[i:i + 200]
            db.table("properties") \
                .update({"suburb": suburb}) \
                .in_("id", chunk) \
                .execute()
            done += len(chunk)
    print(f"Backfilled {done} properties across {len(by_suburb)} suburbs.")


if __name__ == "__main__":
    main()

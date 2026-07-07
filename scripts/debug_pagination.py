from backend.db.client import get_client

def debug_pagination():
    db = get_client()
    start = 0
    batch_size = 1000
    total_rows = 0
    urls = set()
    batch_num = 0

    while True:
        result = (
            db.table("listings")
            .select("image_url")
            .eq("source_id", 2)
            .not_.is_("image_url", "null")
            .order("id")
            .range(start, start + batch_size - 1)
            .execute()
)
        batch = result.data
        batch_num += 1
        print(f"Batch {batch_num}: requested range {start}-{start + batch_size - 1}, got {len(batch)} rows")
        if not batch:
            break
        total_rows += len(batch)
        urls.update(row["image_url"] for row in batch)
        start += batch_size

    print(f"Total rows fetched: {total_rows}")
    print(f"Distinct URLs: {len(urls)}")

if __name__ == "__main__":
    debug_pagination()
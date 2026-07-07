import imagehash
import requests
from io import BytesIO
from PIL import Image
from backend.db.client import get_client


def compute_phash(image_url: str):
    response = requests.get(image_url, timeout=10)
    img = Image.open(BytesIO(response.content))
    return str(imagehash.phash(img))


def get_distinct_image_urls(db):
    urls = set()
    start = 0
    batch_size = 1000
    while True:
        result = (
            db.table("listings")
            .select("image_url")
            .eq("source_id", 1)
            .not_.is_("image_url", "null")
            .order("id")
            .range(start, start + batch_size - 1)
            .execute()
        )
        batch = result.data
        if not batch:
            break
        urls.update(row["image_url"] for row in batch)
        start += batch_size
    return urls


def backfill_phashes():
    db = get_client()
    distinct_urls = get_distinct_image_urls(db)
    print(f"Hashing {len(distinct_urls)} distinct images...")

    for i, url in enumerate(distinct_urls, 1):
        try:
            phash = compute_phash(url)
            db.table("listings").update({"image_phash": phash}).eq("image_url", url).execute()
        except Exception as e:
            print(f"  Failed on {url}: {e}")
            continue
        if i % 100 == 0:
            print(f"  {i}/{len(distinct_urls)} done")


if __name__ == "__main__":
    backfill_phashes()
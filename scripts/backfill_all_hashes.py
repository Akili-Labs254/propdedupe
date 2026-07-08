import imagehash
import requests
from io import BytesIO
from PIL import Image
from backend.db.client import get_client


def compute_hashes(image_url: str):
    response = requests.get(image_url, timeout=10)
    img = Image.open(BytesIO(response.content))
    return {
        "image_phash": str(imagehash.phash(img)),
        "image_ahash": str(imagehash.average_hash(img)),
        "image_whash": str(imagehash.whash(img)),
    }


def get_distinct_image_urls(db, source_id: int):
    urls = set()
    start = 0
    batch_size = 1000
    while True:
        result = (
            db.table("listings")
            .select("image_url")
            .eq("source_id", source_id)
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


def backfill(source_id: int):
    db = get_client()
    distinct_urls = get_distinct_image_urls(db, source_id)
    print(f"Hashing {len(distinct_urls)} distinct images for source_id={source_id}...")

    for i, url in enumerate(distinct_urls, 1):
        try:
            hashes = compute_hashes(url)
            db.table("listings").update(hashes).eq("image_url", url).execute()
        except Exception as e:
            print(f"  Failed on {url}: {e}")
            continue
        if i % 100 == 0:
            print(f"  {i}/{len(distinct_urls)} done")

    print(f"Done source_id={source_id}.")


if __name__ == "__main__":
    backfill(source_id=1)  # BRK
    backfill(source_id=2)  # P24
import imagehash
import requests
from io import BytesIO
from PIL import Image
from backend.common import config
from backend.db.client import get_client


class TransientImageError(Exception):
    """The image may well exist; we just couldn't get it right now.

    Network failures, timeouts, 429 and 5xx. The caller must NOT mark the
    listing as unfetchable: that mark (image_phash = '') is permanent, it
    takes the listing out of image matching for good, and a wifi blip
    during a long hash stage would otherwise do it to hundreds of rows.
    """


def compute_hashes(image_url: str):
    """Download one image and return its three perceptual hashes.

    Raises TransientImageError for anything worth retrying later, and any
    other exception for an image that is genuinely gone or unreadable
    (404/410/403, not an image).
    """
    try:
        response = requests.get(image_url, timeout=15,
                                headers={"User-Agent": config.USER_AGENT})
    except (requests.ConnectionError, requests.Timeout) as e:
        raise TransientImageError(f"{type(e).__name__}: {e}") from e
    if response.status_code == 429 or response.status_code >= 500:
        raise TransientImageError(f"HTTP {response.status_code}")
    # Without this, a 404 page or a CDN error body went straight to PIL.
    response.raise_for_status()
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
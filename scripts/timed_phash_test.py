import time
import imagehash
import requests
from io import BytesIO
from PIL import Image
from backend.db.client import get_client

def compute_phash(image_url: str):
    response = requests.get(image_url, timeout=10)
    img = Image.open(BytesIO(response.content))
    return str(imagehash.phash(img))

def timed_test():
    db = get_client()
    result = (
        db.table("listings")
        .select("image_url")
        .eq("source_id", 2)
        .not_.is_("image_url", "null")
        .limit(20)
        .execute()
    )
    urls = list({row["image_url"] for row in result.data})[:20]

    start = time.time()
    for url in urls:
        compute_phash(url)
    elapsed = time.time() - start

    print(f"{len(urls)} images in {elapsed:.1f}s → {elapsed/len(urls):.2f}s/image")
    print(f"Estimated full run (6996 images): {6996 * (elapsed/len(urls)) / 60:.1f} minutes")

if __name__ == "__main__":
    timed_test()
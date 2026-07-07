import requests
import imagehash
from io import BytesIO
from PIL import Image
from backend.db.client import get_client

def compute_phash(image_url: str):
    response = requests.get(image_url, timeout=10)
    img = Image.open(BytesIO(response.content))
    return str(imagehash.phash(img))

def test_sample():
    db = get_client()
    result = (
        db.table("listings")
        .select("id, image_url")
        .eq("source_id", 2)
        .not_.is_("image_url", "null")
        .limit(5)
        .execute()
    )
    for row in result.data:
        phash = compute_phash(row["image_url"])
        print(f"{row['id']}: {phash}")

if __name__ == "__main__":
    test_sample()
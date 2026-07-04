"""Day-one spike: prove we can fetch and parse ONE real listing.

Throwaway de-risking code — it stores nothing. Run it on day one to find out
whether the target sites are scrapable with plain requests before we build
anything real.

Usage:
    python scripts/spike_fetch.py
"""
import json
import sys
import time

import requests
from bs4 import BeautifulSoup

URL = "https://www.buyrentkenya.com/houses-for-sale/nairobi"
USER_AGENT = "PropDeDupe/0.1 (student portfolio project)"


def main() -> int:
    print(f"Fetching {URL} ...")
    try:
        time.sleep(2)  # be polite
        resp = requests.get(URL, headers={"User-Agent": USER_AGENT}, timeout=30)
        resp.raise_for_status()
    except r
    
        print(f"Fetch failed: {exc}")
        return 1

    soup = BeautifulSoup(resp.text, "html.parser")

    # BuyRentKenya embeds its listings in a JSON-LD <script> block.
    blocks = soup.find_all("script", type="application/ld+json")
    print(f"Found {len(blocks)} JSON-LD block(s).")

    for block in blocks:
        try:
            data = json.loads(block.string or "")
        except (json.JSONDecodeError, TypeError):
            continue

        graph = data.get("@graph", []) if isinstance(data, dict) else []
        for node in graph:
            if node.get("@type") == "WebPage":
                items = node.get("mainEntity", {}).get("itemListElement", [])
                if items:
                    first = items[0].get("item", {})
                    price = (
                        first.get("offers", {})
                        .get("priceSpecification", {})
                        .get("price")
                    )
                    print("\nFirst listing found:")
                    print("  name :", first.get("name"))
                    print("  price:", price)
                    return 0

    print("\nNo listings parsed — inspect the page; its structure may have changed.")
    return 0

if __name__ == "__main__":
    sys.exit(main())

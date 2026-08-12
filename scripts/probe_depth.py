"""How big is each source index, and will it fit on the free tier?

Both portals print their own total on page 1 ("Showing 1 - 20 of 88 815"),
so measuring an index costs one request, not a crawl. That number decides
three things we kept guessing at:

  - whether a category can be crawled to completion at all, which since
    ADR 0008 is the only way its listings ever get staled
  - how much disk a complete crawl would need
  - whether the next unlock is pruning, a paid tier, or narrowing scope

Run from a residential IP — P24 serves 503s to datacenter ranges.

    python -m scripts.probe_depth
    python -m scripts.probe_depth --headroom-mb 205
    python -m scripts.probe_depth --debug        # when a pattern stops matching
"""
import argparse
import random
import re
import time
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from scripts import scrape_buyrentkenya as brk
from scripts import scrape_property24 as p24

# Bytes per listing row including indexes, measured 2026-08-12:
# 245 MB across 55,963 rows in the listings table.
BYTES_PER_LISTING = 4400
FREE_TIER_MB = 500
PER_PAGE = 20

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                         "AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/120.0 Safari/537.36"}

# Match against extracted TEXT, never raw HTML. Both sites wrap the digits
# in tags — "Showing <span>1 - 20</span> of <span>88 815</span>" — so a
# pattern anchored on the markup finds nothing, which is exactly how the
# first version of this script failed on all six indexes.
#
# Thousands separators differ: P24 uses spaces (88 815), BRK commas.
TOTAL_PATTERNS = [
    re.compile(r"Showing\s+[\d\s,.]+?-\s*[\d\s,.]+?\s+of\s+([\d\s,.]+)", re.I),
    re.compile(r"\bof\s+([\d\s,.]{3,}?)\s+(?:propert|result|listing|home)", re.I),
    re.compile(r"([\d\s,.]{3,}?)\s+(?:propert|result|listing|home)\w*\s+"
               r"(?:found|available|for|match)", re.I),
    re.compile(r"([\d\s,.]{3,}?)\s+(?:propert|result|listing)\w*\b", re.I),
]


def page_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" "))


def extract_total(text: str):
    for pat in TOTAL_PATTERNS:
        for m in pat.finditer(text):
            digits = re.sub(r"[^\d]", "", m.group(1))
            # Two digits is the page-size echo ("20 properties"), not a
            # total. Real indexes here are in the thousands.
            if digits and len(digits) >= 3:
                return int(digits)
    return None


def label_for(url: str) -> str:
    """Last two path segments — P24 encodes type in one, BRK in two."""
    parts = [p for p in urlparse(url).path.split("/") if p]
    return "/".join(parts[-2:])[:34]


def probe(label: str, url: str, debug: bool = False):
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
    except Exception as e:
        print(f"  {label:<38} FAILED: {e}")
        return None

    text = page_text(r.text)
    total = extract_total(text)
    if total is None:
        print(f"  {label:<38} total not found — layout may have changed")
        if debug:
            # Print every "<number> <word>" pair so the real phrasing is
            # visible and the pattern can be fixed without guessing.
            hits = re.findall(r"\d[\d\s,.]{2,}\s+\w+", text)[:12]
            print(f"      candidates: {hits or '(no numbers in page text)'}")
            print(f"      opening text: {text[:200]!r}")
        return None

    pages = -(-total // PER_PAGE)
    mb = total * BYTES_PER_LISTING / (1024 * 1024)
    print(f"  {label:<38} {total:>8,} listings {pages:>6,} pages ~{mb:>5.0f} MB")
    return {"label": label, "total": total, "pages": pages, "mb": mb}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--headroom-mb", type=float, default=None,
                    help="free disk left (500MB tier minus current use)")
    ap.add_argument("--debug", action="store_true",
                    help="dump candidate phrases when a total isn't found")
    args = ap.parse_args()

    print("Index sizes as reported by each source's own page 1.\n")
    rows = []

    for name, mod in (("P24", p24), ("BRK", brk)):
        print(f"{name}:")
        for category, urls in mod.CATEGORY_URLS.items():
            if isinstance(urls, str):
                urls = [urls]
            for url in urls:
                got = probe(f"{category:<4} {label_for(url)}", url, args.debug)
                if got:
                    rows.append(got)
                time.sleep(random.uniform(1, 2))
        print()

    if not rows:
        print("Nothing measured. Re-run with --debug to see the page text; "
              "if it looks like a block page, P24 503s datacenter ranges.")
        return

    total_listings = sum(r["total"] for r in rows)
    total_mb = sum(r["mb"] for r in rows)
    print(f"Complete crawl of every index above: "
          f"{total_listings:,} listings, ~{total_mb:,.0f} MB")

    if args.headroom_mb is not None:
        head = args.headroom_mb
        print(f"Headroom given: {head:,.0f} MB")
        if total_mb <= head:
            print("  Fits. Every category can be crawled to completion, so "
                  "staleness works everywhere.")
        else:
            print(f"  Does NOT fit — short by {total_mb - head:,.0f} MB.")
            print(f"  Would need ~{total_mb / FREE_TIER_MB:.1f}x the free tier.")
            print("  Options: prune delisted rows, narrow scope (suburb or "
                  "price band), or leave the free tier.")
            print("  Whatever stays capped never stales — see ADR 0008.")

    print("\nCaveat: these are the portals' own counts, which include their "
          "duplicates and per-unit development listings. The distinct-property "
          "number is much lower — that gap is the product.")


if __name__ == "__main__":
    main()

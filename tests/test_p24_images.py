"""Property24 cover images, and the page-level field-coverage guard.

Around November 2026 P24 changed the cover photo in each listing tile
from an SVG-style <image> element to a plain <img>. The parser selected
only <image>, found nothing, and returned listings with no image. Nothing
errored: the scrape on 24 Nov saved 26,008 listings, the upsert wrote
NULL over every stored image URL, and the hash-invalidation trigger then
cleared their perceptual hashes. Prices were fine, so the run looked
healthy until the hash stage reported "0 hashed" for 4,777 new listings.

It is the same failure as the price in August (tests/test_p24_price.py):
a selector stops matching and the result is a NULL, not an exception.
check_field_coverage turns that into ParserOutOfDate before anything is
saved.
"""

import pytest
from bs4 import BeautifulSoup

from backend.scraper.base import ParserOutOfDate
from backend.scraper.property24 import Property24Scraper, check_field_coverage

IMG = "https://images.prop24.com/3hbos44piuqh6eld5dbge223ua/Crop600x400"


def _tile(n, image_html, price="KSh 18 000"):
    return f"""
    <div class="js_listingTile" data-listing-number="{n}">
      <a href="/1-bedroom-apartment-flat-to-rent-in-syokimau-{n}">
        <span class="js_listingTileImageHolder p24_image">{image_html}</span>
        <span class="p24_price">{price}</span>
        <span class="p24_location">Syokimau</span>
      </a>
    </div>"""


NEW_IMG = f'<img alt="1 Bedroom" class="pull-left" loading="lazy" src="{IMG}"/>'
OLD_IMAGE = f'<svg><image src="{IMG}"></image></svg>'


def _parse_one(html):
    tile = BeautifulSoup(html, "html.parser").select_one("div.js_listingTile")
    return Property24Scraper()._parse_tile(tile)


def test_reads_cover_from_img_tag():
    assert _parse_one(_tile(1, NEW_IMG)).image_urls == [IMG]


def test_still_reads_old_image_tag():
    assert _parse_one(_tile(1, OLD_IMAGE)).image_urls == [IMG]


def test_page_without_any_images_is_refused():
    page = "".join(_tile(n, "") for n in range(1, 21))
    with pytest.raises(ParserOutOfDate, match="cover image"):
        Property24Scraper().parse(page)


def test_page_without_any_prices_is_refused():
    page = "".join(_tile(n, NEW_IMG, price="") for n in range(1, 21))
    with pytest.raises(ParserOutOfDate, match="price"):
        Property24Scraper().parse(page)


def test_some_missing_images_are_fine():
    """Individual adverts without a photo are normal; only a whole page
    without one means the selector broke."""
    page = _tile(1, "") + "".join(_tile(n, NEW_IMG) for n in range(2, 21))
    assert len(Property24Scraper().parse(page)) == 20


def test_small_pages_are_not_judged():
    """The last page of a crawl can hold a couple of listings; two
    photo-less adverts there prove nothing about the markup."""
    page = _tile(1, "") + _tile(2, "")
    assert len(Property24Scraper().parse(page)) == 2


def test_empty_page_still_returns_empty():
    """An empty page is how pagination ends; it must not raise."""
    assert Property24Scraper().parse("<html><body></body></html>") == []
    check_field_coverage([])

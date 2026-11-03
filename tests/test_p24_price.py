"""Price parsing for Property24.

P24 dropped the `content` attribute from span.p24_price around 26 August
2026 and moved the value into the element's text. The parser read only the
attribute, so it returned None for every listing: 10,083 sale listings and
~30,000 rentals lost their price over two scrapes.

Nothing errored. The listings still parsed, still had titles, beds, images
and URLs. Only one field collapsed, and the site simply showed fewer priced
results, which looked like POA adverts. It surfaced eleven days later, while
checking why an ad landing page had 1,811 properties and 23 prices.

`price_history` is what dated it: 10,213 rows in the week of 24 Aug and
26,748 in the week of 31 Aug, every one recording a price becoming NULL.
"""

import pytest
from bs4 import BeautifulSoup

from backend.scraper.property24 import Property24Scraper, parse_price_text


@pytest.mark.parametrize("text,expected", [
    ("KSh 145 000 000", 145000000.0),
    ("KSh 72,000,000", 72000000.0),
    ("KSh 4 500", 4500.0),
    ("145000000", 145000000.0),
])
def test_prices_parse(text, expected):
    assert parse_price_text(text) == expected


def test_non_breaking_spaces():
    """P24 separates digit groups with NBSP. A character class that only
    covers ordinary spaces silently truncates the number: this bit us once
    already in probe_depth.py."""
    assert parse_price_text("KSh 145 000 000") == 145000000.0
    assert parse_price_text("KSh 12 500 000") == 12500000.0


@pytest.mark.parametrize("text", ["POA", "Price on Application", "", None, "—"])
def test_poa_is_none_never_zero(text):
    """Standing rule: POA is NULL. A 0 sorts to the top of every
    price-ascending page, which is where the ad traffic lands."""
    assert parse_price_text(text) is None


def test_zero_is_none():
    assert parse_price_text("KSh 0") is None


def test_range_takes_lower_bound_not_concatenation():
    """"KSh 1 200 000 - 2 000 000" must not become 12000002000000."""
    assert parse_price_text("KSh 1 200 000 - 2 000 000") == 1200000.0


def test_reads_price_from_text_when_attribute_absent():
    """The actual regression: markup with no content attribute."""
    html = """
    <div class="js_listingTile" data-listing-number="123">
      <span class="p24_price">KSh 145 000 000</span>
      <meta itemprop="name" content="5 Bedroom House">
    </div>
    """
    tile = BeautifulSoup(html, "html.parser").select_one("div.js_listingTile")
    listing = Property24Scraper()._parse_tile(tile)
    assert listing.price == 145000000.0


def test_still_reads_content_attribute_if_it_returns():
    html = """
    <div class="js_listingTile" data-listing-number="123">
      <span class="p24_price" content="72000000">KSh 72 000 000</span>
      <meta itemprop="name" content="5 Bedroom House">
    </div>
    """
    tile = BeautifulSoup(html, "html.parser").select_one("div.js_listingTile")
    assert Property24Scraper()._parse_tile(tile).price == 72000000.0

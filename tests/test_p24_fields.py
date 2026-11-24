"""Property24 tile fields, against trimmed copies of real 24 Nov 2026 tiles.

The structure is kept exactly (element nesting, classes, schema.org
blocks); long attribute values, SVG icons and the excerpt are cut.

Two things these pin:

* The title comes from the tile link, not the first itemprop="name". Rent
  tiles have no schema.org/Product block, only the agency's
  RealEstateAgent one, so "first meta name" was the agency: ~11,300 active
  rentals were titled with an agency's name and ~35,400 had none, and the
  site rendered "None" as rent property headings.
* Sizes read the whole number and only in m². "102 120 m²" was stored as
  102 and "55 acres" as 55 m².
"""
import pytest
from bs4 import BeautifulSoup

from backend.scraper.property24 import (Property24Scraper, parse_price_text,
                                        parse_size_text)

RENT_TILE = """
<div class="MoonsForking js_listingTile" data-listing-number="117070485">
<div class="p24_regularTile" data-listing-number="117070485">
<a href="/2-bedroom-apartment-flat-to-rent-in-spring-valley-117070485"
   title="2 Bedroom Apartment / Flat to Rent in Spring Valley">
<span class="js_listingTileImageHolder p24_image">
<img alt="2 Bedroom Apartment / Flat to Rent in Spring Valley" class="pull-left"
     loading="lazy" src="https://images.prop24.com/a4tkhjaagurufxvdo6orbn75ni/Crop600x400">
<ul class="p24_badges"><li class="p24_availableBadge">AVAILABLE</li></ul>
</img></span>
<span class="p24_content">
<span class="p24_branding js_agencyBrandingLink" itemscope=""
      itemtype="http://schema.org/RealEstateAgent" title="Symec Properties">
<img alt="Symec Properties" itemprop="image" src="https://images.prop24.com/187780273/Ensure264x153">
<meta content="Symec Properties" itemprop="name"/>
</img></span>
<span>
<span class="p24_price">
                        KSh 140 000
                    </span>
<span class="p24_propertyTitle">2 Bedroom Apartment / Flat</span>
<span class="p24_location">Spring Valley</span>
<span class="p24_address">PQVR+GQX Brookside Dr, Spring Valley, Nairobi</span>
</span>
<span class="p24_icons">
<span class="p24_featureDetails" title="Bedrooms"><svg><image alt="Beds"/></svg><span>2</span></span>
<span class="p24_featureDetails" title="Bathrooms"><svg><image alt="Bathroom"/></svg><span>3</span></span>
<span class="p24_size" title="Floor Size"><image alt="Floor Size"/><span>200 m²</span></span>
<span class="p24_favourite"><img alt="Add Favourite" title="Add Favourite"
      src="https://static.sa-property.co.za/Content/Images/spui_favourite_disabled.svg"></img></span>
</span>
</span>
</a>
</div>
</div>
"""

SALE_TILE = """
<div class="FlayingCancer js_listingTile" data-listing-number="117379307">
<div class="p24_regularTile" data-listing-number="117379307" itemscope=""
     itemtype="http://schema.org/Product">
<meta content="4 Bedroom House for Sale in Kiambu Road" itemprop="name">
<span hidden="" itemprop="brand" itemscope="" itemtype="http://schema.org/Brand">
<meta content="Villa Lee Properties" itemprop="name"/>
</span>
<div itemprop="offers" itemscope="" itemtype="http://schema.org/Offer">
<meta content="30000000.00" itemprop="price"/>
<span hidden="" itemprop="seller" itemscope="" itemtype="http://schema.org/RealEstateAgent">
<meta content="Villa Lee Properties" itemprop="name"/>
</span>
</div>
<a href="/4-bedroom-house-for-sale-in-kiambu-road-117379307"
   title="4 Bedroom House for Sale in Kiambu Road">
<span class="js_listingTileImageHolder p24_image">
<img alt="4 Bedroom House for Sale in Kiambu Road" class="pull-left" loading="lazy"
     src="https://images.prop24.com/rmzuzsmbolphrkgvk76lgo67a4/Crop600x400"/>
</span>
<span class="p24_content">
<span class="p24_branding js_agencyBrandingLink" itemscope=""
      itemtype="http://schema.org/RealEstateAgent" title="Villa Lee Properties">
<img alt="Villa Lee Properties" itemprop="image" src="https://images.prop24.com/148274194/Ensure264x153"/>
<meta content="Villa Lee Properties" itemprop="name"/>
</span>
<span>
<span class="p24_price">
                        KSh 30 000 000
                    </span>
<span class="p24_propertyTitle">4 Bedroom House</span>
<span class="p24_location">Kiambu Road</span>
<span class="p24_address">RR6X+9G Kiambu, Kiambu Road, Nairobi</span>
</span>
<span class="p24_icons">
<span class="p24_featureDetails" title="Bedrooms"><svg><image alt="Beds"/></svg><span>4</span></span>
<span class="p24_featureDetails" title="Bathrooms"><svg><image alt="Bathroom"/></svg><span>5.5</span></span>
<span class="p24_size" title="Floor Size"><image alt="Floor Size"/><span>234 m²</span></span>
</span>
</span>
</a>
</meta></div>
</div>
"""


def parse_tile(html):
    tile = BeautifulSoup(html, "html.parser").select_one("div.js_listingTile")
    return Property24Scraper()._parse_tile(tile)


# ── real tiles, every field ────────────────────────────────────────────

def test_rent_tile_fields():
    l = parse_tile(RENT_TILE)
    assert l.source_listing_id == "117070485"
    assert l.title == "2 Bedroom Apartment / Flat to Rent in Spring Valley"
    assert l.price == 140000.0
    assert l.location == "Spring Valley"
    assert l.address == "PQVR+GQX Brookside Dr, Spring Valley, Nairobi"
    assert l.bedrooms == 2 and l.baths == 3.0
    assert l.size_sqm == 200.0
    assert l.image_urls == [
        "https://images.prop24.com/a4tkhjaagurufxvdo6orbn75ni/Crop600x400"]
    assert l.url.endswith("/2-bedroom-apartment-flat-to-rent-in-spring-valley-117070485")


def test_sale_tile_fields():
    l = parse_tile(SALE_TILE)
    assert l.title == "4 Bedroom House for Sale in Kiambu Road"
    assert l.price == 30000000.0
    assert l.bedrooms == 4 and l.baths == 5.5
    assert l.size_sqm == 234.0
    assert l.image_urls == [
        "https://images.prop24.com/rmzuzsmbolphrkgvk76lgo67a4/Crop600x400"]


def test_rent_title_is_never_the_agency():
    assert parse_tile(RENT_TILE).title != "Symec Properties"


def test_cover_image_is_never_the_agency_logo():
    for tile in (RENT_TILE, SALE_TILE):
        assert "Ensure264x153" not in parse_tile(tile).image_urls[0]


def test_title_falls_back_past_agency_meta_to_visible_title():
    """No link title: skip the agency's meta, use the visible title."""
    html = RENT_TILE.replace(
        'title="2 Bedroom Apartment / Flat to Rent in Spring Valley">\n<span class="js_',
        '>\n<span class="js_')
    assert parse_tile(html).title == "2 Bedroom Apartment / Flat"


def test_tile_without_an_id_is_skipped():
    html = RENT_TILE.replace('data-listing-number="117070485"', "", 1)
    assert parse_tile(html) is None


def test_two_real_tiles_parse_as_a_page():
    """Fewer than the coverage minimum: never judged, both parsed."""
    assert len(Property24Scraper().parse(RENT_TILE + SALE_TILE)) == 2


# ── sizes ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("200 m²", 200.0),
    ("1 200 m²", 1200.0),
    ("1 200 m²", 1200.0),
    ("102 120 m²", 102120.0),     # was 102; now sane_size drops it as a plot
    ("85.5 m²", 85.5),
    ("55 acres", None),           # was 55 m²
    ("0.5 acres", None),
    ("2 ha", None),
    ("", None),
    (None, None),
])
def test_sizes(text, expected):
    assert parse_size_text(text) == expected


# ── currency ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", ["USD 1 200", "$ 2 500", "US$ 450 000", "€ 900"])
def test_foreign_currency_is_refused(text):
    """Stored prices are all KES; a dollar figure read as KSh is ~130x off."""
    assert parse_price_text(text) is None


def test_shillings_still_parse():
    assert parse_price_text("KSh 140 000") == 140000.0

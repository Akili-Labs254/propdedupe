"""BuyRentKenya parser tests, against saved copies of the real markup.

Written 2026-09-01, the night BRK re-platformed. The parser returned zero
listings from every page and **all 113 existing tests still passed** —
nothing exercised parse() against real HTML. That is the same gap that let
the stale-detection bug through in August: the code that broke was the code
without tests.

The fixtures are deliberately whole pages rather than trimmed snippets. A
snippet only proves the parser handles what we already understood; the full
page is what catches the next change we haven't imagined.

Refresh them with:
    python - <<'EOF'
    import requests, pathlib
    for name, url in [
        ("brk_houses_for_sale.html", "https://www.buyrentkenya.com/houses-for-sale"),
        ("brk_flats_for_rent.html",  "https://www.buyrentkenya.com/flats-apartments-for-rent"),
    ]:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        pathlib.Path("tests/fixtures", name).write_text(r.text, encoding="utf-8")
    EOF
"""

from pathlib import Path

import pytest

from backend.scraper.base import ParserOutOfDate
from backend.scraper.buyrentkenya import (
    BuyRentKenyaScraper,
    _canonical_image,
    _item_list_elements,
    _node_types,
)

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def sale_listings():
    return BuyRentKenyaScraper().parse(load("brk_houses_for_sale.html"))


@pytest.fixture(scope="module")
def rent_listings():
    return BuyRentKenyaScraper().parse(load("brk_flats_for_rent.html"))


# ── the failure that started this ────────────────────────────────────

def test_sale_page_yields_listings(sale_listings):
    """The bug: parse() returned [] and the scraper read that as the end
    of the list. Any assertion above zero would have caught it."""
    assert len(sale_listings) == 25


def test_rent_page_yields_listings(rent_listings):
    # Rent used to need a special case (its JSON-LD omitted the Product
    # url). Under the new markup neither category carries Product data at
    # all, so both go through the same card path — assert it, don't assume.
    assert len(rent_listings) == 25


# ── identity: the near-miss that would have doubled the corpus ───────

def test_listing_ids_keep_the_item_prefix(sale_listings):
    """save_listings upserts on (source_id, source_listing_id). Emitting a
    bare "4052696" where the table holds "item-4052696" would insert every
    listing as new and orphan ~13,800 existing rows."""
    assert all(l.source_listing_id.startswith("item-") for l in sale_listings)
    assert all(l.source_listing_id[5:].isdigit() for l in sale_listings)


def test_listing_ids_are_unique(sale_listings):
    ids = [l.source_listing_id for l in sale_listings]
    assert len(set(ids)) == len(ids)


# ── field extraction ────────────────────────────────────────────────

def test_core_fields_populated(sale_listings):
    for l in sale_listings:
        assert l.title, f"missing title: {l.source_listing_id}"
        assert l.url and "/listings/" in l.url
        assert l.bedrooms is not None
        assert l.location


def test_price_is_null_never_zero(sale_listings):
    """Standing rule: POA is NULL. A parsed 0 would sort to the top of
    every price-ascending page, which is where the ad traffic lands."""
    assert all(l.price != 0 for l in sale_listings)
    priced = [l for l in sale_listings if l.price is not None]
    # A handful of POA adverts is normal; a majority means the price
    # selector has drifted.
    assert len(priced) >= 20


def test_description_label_is_not_used_as_title(sale_listings):
    """Cards carry two <h3>s — a literal "Description" label and the real
    title. Taking the first blindly names every listing "Description"."""
    assert all(l.title.strip().casefold() != "description"
               for l in sale_listings)


def test_hero_image_is_not_the_agency_logo(sale_listings):
    """The first <img> in a card is the agent's badge. Hashing that would
    weld an agency's whole portfolio into one property — the exact failure
    CK_GROUP_CAP exists to catch downstream.

    This failed on its first run: agency logos sit at
    /agencies/<uuid>/logo_x.webp, and the parser's UUID preference matched
    the agency's id and returned the logo. Hence checking the whole URL.
    """
    for l in sale_listings:
        assert l.image_urls, f"no image: {l.source_listing_id}"
        url = l.image_urls[0].casefold()
        for marker in ("logo", "/agencies/", "placeholder", "watermark"):
            assert marker not in url, f"{marker!r} in hero image: {url}"


def test_hero_images_are_distinct_across_listings(sale_listings):
    """Twenty-five cards resolving to one image means branding is winning
    again — a different mistake than the one above, same symptom."""
    heroes = [l.image_urls[0] for l in sale_listings if l.image_urls]
    assert len(set(heroes)) >= len(heroes) - 2


def test_images_use_one_canonical_rendition(sale_listings):
    """The CDN serves the same photo as _cardMobile or _medium depending on
    the breakpoint. cluster_key hashes image_url, so an uncanonicalised URL
    splits identical listings on nothing but which variant rendered."""
    assert all(l.image_urls[0].endswith("_medium.webp") for l in sale_listings)


def test_plot_acreage_does_not_become_size_sqm(sale_listings):
    """Cards show plot acreage; P24 reports floor area. Mixing them would
    corrupt both cluster_key and the scorer's size signal."""
    assert all(l.size_sqm is None for l in sale_listings)


def test_baths_parsed_when_present(sale_listings):
    withbaths = [l for l in sale_listings if l.baths is not None]
    assert len(withbaths) >= 20


# ── the guard: silence must become noise ─────────────────────────────

def test_raises_when_cards_vanish_but_json_ld_remains():
    html = """
    <script type="application/ld+json">
    {"@graph": [{"@type": "ItemList",
                 "itemListElement": [
                    {"@type": "ListItem", "position": 1,
                     "url": "https://www.buyrentkenya.com/listings/a-house-1"}]}]}
    </script>
    """
    with pytest.raises(ParserOutOfDate):
        BuyRentKenyaScraper().parse(html)


def test_raises_when_json_ld_vanishes_but_cards_remain():
    html = '<a href="/listings/a-house-1">A house</a>'
    with pytest.raises(ParserOutOfDate):
        BuyRentKenyaScraper().parse(html)


def test_genuinely_empty_page_returns_empty_not_raises():
    """The end of pagination is a real, expected state and must stay
    distinguishable from a broken parser — otherwise the crawl can never
    terminate normally."""
    assert BuyRentKenyaScraper().parse("<html><body></body></html>") == []


# ── the two shape changes, unit level ───────────────────────────────

def test_node_types_handles_string_and_list():
    """@type was "WebPage" before the re-platform and
    ["WebPage", "SearchResultsPage"] after. Comparing a string to a list
    matches nothing, silently."""
    assert _node_types({"@type": "WebPage"}) == ["WebPage"]
    assert "WebPage" in _node_types({"@type": ["WebPage", "SearchResultsPage"]})
    assert _node_types({}) == []


def test_item_list_found_in_both_graph_shapes():
    current = [{"@type": ["WebPage", "SearchResultsPage"]},
               {"@type": "ItemList", "itemListElement": [{"position": 1}]}]
    legacy = [{"@type": "WebPage",
               "mainEntity": {"itemListElement": [{"position": 1}]}}]
    assert len(_item_list_elements(current)) == 1
    assert len(_item_list_elements(legacy)) == 1
    assert _item_list_elements([{"@type": "Organization"}]) == []


def test_canonical_image_handles_missing_extension():
    base = "https://cdn.example/1/abc.jpeg"
    assert _canonical_image(base + "_cardMobile.webp") == base + "_medium.webp"
    assert _canonical_image(base + "_medium.webp") == base + "_medium.webp"
    # Some files carry no source extension at all.
    assert (_canonical_image("https://cdn.example/1/4d8a61011490_cardMobile.webp")
            == "https://cdn.example/1/4d8a61011490_medium.webp")

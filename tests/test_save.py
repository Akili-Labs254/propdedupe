"""Price sanity + cluster_key tests.

The bounds exist because the portals carry genuine junk: a KSh 2 "monthly
rent", a KSh 300,000,000/month flat, a house "for sale" at KSh 0. Those
numbers reached the live site: a bait listing at KSh 8,500 sorted to the
top of price-ascending search, so the parse layer now refuses them.

Run: pytest tests/test_save.py
"""
from types import SimpleNamespace

import pytest

import backend.db.save as save_mod
from backend.db.save import (compute_cluster_key, sane_price, sane_size,
                             save_listings)


def listing(**kw):
    base = dict(image_urls=["http://img/1.jpg"], bedrooms=2, baths=2.0,
                size_sqm=100.0, price=85000.0)
    base.update(kw)
    return SimpleNamespace(**base)


# ── plausible prices survive untouched ───────────────────────────────

def test_normal_prices_pass_through():
    assert sane_price(85000.0, "rent") == 85000.0
    assert sane_price(33_000_000.0, "sale") == 33_000_000.0


def test_none_stays_none():
    """POA listings carry price=None and must not become 0."""
    assert sane_price(None, "rent") is None
    assert sane_price(None, "sale") is None


def test_boundaries_are_inclusive():
    assert sane_price(3_000, "rent") == 3_000
    assert sane_price(5_000_000, "rent") == 5_000_000
    assert sane_price(500_000, "sale") == 500_000


# ── the junk we actually found in the corpus ─────────────────────────

def test_junk_rent_nulled():
    assert sane_price(2.0, "rent") is None          # seen live
    assert sane_price(0.0, "rent") is None
    assert sane_price(2_900.0, "rent") is None
    assert sane_price(300_000_000.0, "rent") is None  # KSh 300m/month


def test_plausible_luxury_rent_survives():
    """1m-5m/month is only 13 listings but serviced penthouses are real:
    the cut deliberately sits above them."""
    assert sane_price(1_099_050.0, "rent") == 1_099_050.0
    assert sane_price(2_000_000.0, "rent") == 2_000_000.0


def test_junk_sale_nulled():
    assert sane_price(0.0, "sale") is None
    assert sane_price(490_200.0, "sale") is None


def test_cheap_but_possible_sale_survives():
    assert sane_price(1_900_000.0, "sale") == 1_900_000.0


def test_unknown_category_is_permissive():
    """Never silently drop data for a category we haven't bounded."""
    assert sane_price(1.0, "commercial") == 1.0


# ── floor area ───────────────────────────────────────────────────────

def test_plausible_sizes_pass_through():
    assert sane_size(45.0) == 45.0
    assert sane_size(180.0) == 180.0
    assert sane_size(800.0) == 800.0      # large Karen/Runda house


def test_size_none_stays_none():
    assert sane_size(None) is None


def test_size_boundaries_inclusive():
    assert sane_size(10.0) == 10.0
    assert sane_size(2000.0) == 2000.0


def test_junk_sizes_nulled():
    assert sane_size(1.0) is None          # seen live
    assert sane_size(0.0) is None
    assert sane_size(542331.0) is None     # 54 hectares, seen live
    assert sane_size(2500.0) is None


def test_small_bedsitter_survives():
    """20-25 m² is 189 real listings: the floor deliberately sits below."""
    assert sane_size(22.0) == 22.0


def test_junk_size_nulled_through_save(monkeypatch):
    rows = run_save(monkeypatch, [scrapable("666", size_sqm=1.0)],
                    stored={}, category="rent")
    assert rows[0]["size_sqm"] is None


def test_cluster_key_changes_when_size_nulled():
    """size is hashed into cluster_key, so sanitising must happen first."""
    assert (compute_cluster_key(listing(size_sqm=1.0))
            != compute_cluster_key(listing(size_sqm=None)))


# ── cluster_key must hash the value we store ─────────────────────────

def test_cluster_key_changes_when_price_nulled():
    """Regression guard: sanitising after computing the key would leave
    the key describing a price the row no longer holds."""
    priced = compute_cluster_key(listing(price=2.0))
    nulled = compute_cluster_key(listing(price=None))
    assert priced != nulled


def test_cluster_key_stable_for_same_input():
    assert compute_cluster_key(listing()) == compute_cluster_key(listing())


# ── category is a hard partition at write time ───────────────────────

class FakeTable:
    def __init__(self, sink):
        self.sink = sink

    def __getattr__(self, name):
        def chain(*a, **kw):
            return self
        return chain

    def upsert(self, rows, **kw):
        self.sink.extend(rows)
        return self

    def execute(self):
        return SimpleNamespace(data=[])


def scrapable(sid, **kw):
    base = dict(source_listing_id=sid, url=f"http://p24/{sid}", title="t",
                price=85000.0, location="Westlands", bedrooms=2, baths=2.0,
                size_sqm=100.0, address=None, raw={}, image_urls=[])
    base.update(kw)
    return SimpleNamespace(**base)


def run_save(monkeypatch, incoming, stored, category):
    """Returns the rows that would actually be upserted."""
    sink = []
    monkeypatch.setattr(save_mod, "get_client", lambda: SimpleNamespace(
        table=lambda name: FakeTable(sink)))
    # `stored` maps id -> category (str) or -> a full stored row (dict).
    rows = {k: (v if isinstance(v, dict) else {"category": v})
            for k, v in stored.items()}
    monkeypatch.setattr(save_mod, "_existing_rows",
                        lambda db, sid, ids: rows)
    save_listings(incoming, source_id=2, category=category)
    return sink


def test_category_conflict_is_skipped(monkeypatch):
    """Regression: the upsert key omits category, so a rent scrape could
    silently overwrite a sale row's category and price. That's the shape
    of the price_history corruption found 2026-08."""
    rows = run_save(monkeypatch,
                    [scrapable("111"), scrapable("222")],
                    stored={"111": "sale"},          # 111 already a sale row
                    category="rent")
    saved = {r["source_listing_id"] for r in rows}
    assert saved == {"222"}                          # 111 refused


def test_matching_category_still_saves(monkeypatch):
    rows = run_save(monkeypatch, [scrapable("333")],
                    stored={"333": "rent"}, category="rent")
    assert len(rows) == 1
    assert rows[0]["category"] == "rent"


def test_new_listing_saves_regardless(monkeypatch):
    """No stored row means no conflict: first sighting always saves."""
    rows = run_save(monkeypatch, [scrapable("444")], stored={}, category="rent")
    assert len(rows) == 1


def test_junk_price_still_nulled_through_save(monkeypatch):
    rows = run_save(monkeypatch, [scrapable("555", price=2.0)],
                    stored={}, category="rent")
    assert rows[0]["price"] is None


# ── a parser that misses a field must not erase it ────────────────────
#
# Both P24 incidents were one selector silently matching nothing: prices
# in August (37,000 NULLed), cover images in November (26,008 image URLs
# NULLed, and their hashes with them). Nothing failed; the upsert simply
# wrote NULL over what was stored.

IMG = "https://images.prop24.com/abc/Crop600x400"


def stored_row(**kw):
    base = dict(category="rent", url="http://p24/x", title="2 Bedroom Flat",
                location="Westlands", address="Westlands, Nairobi",
                image_url=IMG, bedrooms=2, baths=2.0, size_sqm=100.0,
                price=85000.0)
    base.update(kw)
    return base


def test_missing_image_keeps_the_stored_one(monkeypatch):
    rows = run_save(monkeypatch, [scrapable("1", image_urls=[])],
                    stored={"1": stored_row()}, category="rent")
    assert rows[0]["image_url"] == IMG


def test_missing_title_keeps_the_stored_one(monkeypatch):
    rows = run_save(monkeypatch, [scrapable("1", title=None)],
                    stored={"1": stored_row(title="Kept")}, category="rent")
    assert rows[0]["title"] == "Kept"


def test_new_value_still_replaces_the_stored_one(monkeypatch):
    rows = run_save(monkeypatch,
                    [scrapable("1", title="New title", image_urls=["http://new"])],
                    stored={"1": stored_row()}, category="rent")
    assert rows[0]["title"] == "New title"
    assert rows[0]["image_url"] == "http://new"


def test_cluster_key_uses_the_kept_image(monkeypatch):
    """The key must describe the stored row, including a kept image."""
    rows = run_save(monkeypatch, [scrapable("1", image_urls=[])],
                    stored={"1": stored_row()}, category="rent")
    assert rows[0]["cluster_key"] is not None


def test_one_listing_losing_its_price_is_allowed(monkeypatch):
    """An advert can genuinely go to Price on Application."""
    incoming = [scrapable(str(i), image_urls=[IMG]) for i in range(10)]
    incoming[0].price = None
    stored = {str(i): stored_row() for i in range(10)}
    rows = run_save(monkeypatch, incoming, stored=stored, category="rent")
    assert len(rows) == 10
    assert rows[0]["price"] is None


@pytest.mark.parametrize("field,attr,empty", [
    ("price", "price", None),
    ("image_url", "image_urls", []),
    ("title", "title", None),
    ("location", "location", None),
])
def test_page_wide_collapse_raises_before_writing(monkeypatch, field, attr, empty):
    incoming = [scrapable(str(i), image_urls=[IMG]) for i in range(20)]
    for l in incoming:
        setattr(l, attr, empty)
    stored = {str(i): stored_row() for i in range(20)}
    with pytest.raises(save_mod.FieldCollapse, match=field):
        run_save(monkeypatch, incoming, stored=stored, category="rent")


def test_collapse_needs_enough_stored_rows(monkeypatch):
    """Two listings is not enough evidence of a broken parser."""
    incoming = [scrapable("1", price=None, image_urls=[IMG]),
                scrapable("2", price=None, image_urls=[IMG])]
    stored = {"1": stored_row(), "2": stored_row()}
    rows = run_save(monkeypatch, incoming, stored=stored, category="rent")
    assert len(rows) == 2


def test_new_listings_are_not_judged_for_collapse(monkeypatch):
    """Nothing stored means nothing to lose; the parser-level guard
    (check_field_coverage) covers all-new pages."""
    incoming = [scrapable(str(i), price=None) for i in range(20)]
    rows = run_save(monkeypatch, incoming, stored={}, category="rent")
    assert len(rows) == 20


def test_collapse_is_a_parser_error():
    """The scrape loop and refresh treat it like any broken parser."""
    from backend.scraper.base import ParserOutOfDate
    assert issubclass(save_mod.FieldCollapse, ParserOutOfDate)


# ── batch hygiene ──────────────────────────────────────────────────────

def test_listing_without_an_id_is_skipped(monkeypatch):
    """source_listing_id is NOT NULL; one bad row would fail the batch."""
    rows = run_save(monkeypatch, [scrapable(None), scrapable("2")],
                    stored={}, category="rent")
    assert [r["source_listing_id"] for r in rows] == ["2"]


def test_duplicate_ids_in_one_page_are_collapsed(monkeypatch):
    """Postgres refuses an upsert that touches one row twice."""
    rows = run_save(monkeypatch,
                    [scrapable("7", price=90000.0), scrapable("7", price=95000.0)],
                    stored={}, category="rent")
    assert len(rows) == 1
    assert rows[0]["price"] == 95000.0


# ── no image, no exact-duplicate key ───────────────────────────────────

def test_no_image_means_no_cluster_key():
    """Without the shared image the key is just beds/baths/price, shared
    by thousands of unrelated flats. Step 2 would merge them all."""
    assert compute_cluster_key(listing(image_urls=[])) is None


def test_no_image_listing_saves_with_no_key(monkeypatch):
    rows = run_save(monkeypatch, [scrapable("9", image_urls=[])],
                    stored={}, category="rent")
    assert rows[0]["cluster_key"] is None


def test_size_is_not_sticky(monkeypatch):
    """The parser returns no size on purpose for acres and junk figures;
    keeping the stored value would preserve an old misparse forever."""
    rows = run_save(monkeypatch, [scrapable("1", size_sqm=None, image_urls=[IMG])],
                    stored={"1": stored_row(size_sqm=102.0)}, category="rent")
    assert rows[0]["size_sqm"] is None

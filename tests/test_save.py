"""Price sanity + cluster_key tests.

The bounds exist because the portals carry genuine junk: a KSh 2 "monthly
rent", a KSh 300,000,000/month flat, a house "for sale" at KSh 0. Those
numbers reached the live site — a bait listing at KSh 8,500 sorted to the
top of price-ascending search — so the parse layer now refuses them.

Run: pytest tests/test_save.py
"""
from types import SimpleNamespace

import backend.db.save as save_mod
from backend.db.save import compute_cluster_key, sane_price, save_listings


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
    """1m-5m/month is only 13 listings but serviced penthouses are real —
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
    monkeypatch.setattr(save_mod, "_existing_categories",
                        lambda db, sid, ids: stored)
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
    """No stored row means no conflict — first sighting always saves."""
    rows = run_save(monkeypatch, [scrapable("444")], stored={}, category="rent")
    assert len(rows) == 1


def test_junk_price_still_nulled_through_save(monkeypatch):
    rows = run_save(monkeypatch, [scrapable("555", price=2.0)],
                    stored={}, category="rent")
    assert rows[0]["price"] is None

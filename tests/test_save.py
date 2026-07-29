"""Price sanity + cluster_key tests.

The bounds exist because the portals carry genuine junk: a KSh 2 "monthly
rent", a KSh 300,000,000/month flat, a house "for sale" at KSh 0. Those
numbers reached the live site — a bait listing at KSh 8,500 sorted to the
top of price-ascending search — so the parse layer now refuses them.

Run: pytest tests/test_save.py
"""
from types import SimpleNamespace

from backend.db.save import compute_cluster_key, sane_price


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

"""Stale detection rules.

refresh.py is the only code that mass-mutates listing status, and until
now it had no tests. On 2026-08-12 a capped rent scrape marked 7,240 live
P24 rentals stale or delisted — 41% of everything then flagged as gone —
because "not seen this run" was read as "removed from the site" when the
scrape had simply stopped at page 1,500 and never looked further.

Every test here pins a rule that would have prevented that, or a guard
that already existed and must not regress.

Run: pytest tests/test_refresh.py
"""
from datetime import datetime, timezone

import pytest

import scripts.refresh as refresh


RUN_START = datetime(2026, 8, 12, 0, 52, tzinfo=timezone.utc)


class FakeDB:
    """Records which (source, category) pairs had statuses flipped."""

    def __init__(self):
        self.flips = []


@pytest.fixture
def flips(monkeypatch):
    """Capture _flip_status_chunked calls instead of hitting Supabase."""
    calls = []

    def fake_flip(db, sid, category, from_status, new_status,
                  before_field, before_value, chunk=1000):
        calls.append((sid, category, from_status, new_status))
        return 0

    monkeypatch.setattr(refresh, "_flip_status_chunked", fake_flip)
    return calls


def result(saved, complete, pre_active, categories=("sale", "rent")):
    """Build one source's stage_scrape result."""
    return {
        "saved": saved * len(categories),
        "pre_active": pre_active * len(categories),
        "categories": {c: {"saved": saved, "complete": complete,
                           "pre_active": pre_active} for c in categories},
    }


def staled(calls, sid, category):
    return (sid, category, "active", "stale") in calls


# ── the 2026-08-12 bug ───────────────────────────────────────────────

def test_capped_scrape_does_not_stale(flips):
    """THE regression test. A scrape that stopped at its page cap proves
    nothing about the listings it never reached."""
    results = {2: result(saved=31500, complete=False, pre_active=21922)}
    refresh.stage_stale(FakeDB(), RUN_START, results)
    assert not staled(flips, 2, "rent")
    assert flips == [], "a capped scrape must not flip any status"


def test_capped_rent_does_not_block_completed_sale(flips):
    """The categories are crawled to different depths, so they cannot
    share one decision — sale finished and must still be staled."""
    results = {2: {
        "saved": 42742, "pre_active": 32723,
        "categories": {
            "sale": {"saved": 11242, "complete": True, "pre_active": 10801},
            "rent": {"saved": 31500, "complete": False, "pre_active": 21922},
        },
    }}
    refresh.stage_stale(FakeDB(), RUN_START, results)
    assert staled(flips, 2, "sale")
    assert not staled(flips, 2, "rent")


def test_complete_scrape_stales_normally(flips):
    results = {1: result(saved=5400, complete=True, pre_active=5422)}
    refresh.stage_stale(FakeDB(), RUN_START, results)
    assert staled(flips, 1, "sale")
    assert staled(flips, 1, "rent")


# ── guards that already existed and must not regress ─────────────────

def test_unscraped_source_is_skipped(flips):
    """CI runs BRK only (P24 blocks datacenter IPs). P24 has no
    'seen this run' signal, so staling would condemn its whole corpus."""
    results = {1: result(saved=5400, complete=True, pre_active=5422)}
    refresh.stage_stale(FakeDB(), RUN_START, results)
    assert not any(sid == 2 for sid, *_ in flips)


def test_low_yield_scrape_is_skipped(flips):
    """A parser that broke mid-run, or a block that looked like the end
    of pagination. Saved 10% of the prior active count — do not trust it."""
    results = {2: result(saved=500, complete=True, pre_active=5000)}
    refresh.stage_stale(FakeDB(), RUN_START, results)
    assert flips == []


def test_crashed_category_is_never_complete(flips):
    """stage_scrape records complete=False on a crash; a crashed scrape
    leaves the tail of the list unseen."""
    results = {2: result(saved=0, complete=False, pre_active=5000)}
    refresh.stage_stale(FakeDB(), RUN_START, results)
    assert flips == []


# ── --force ──────────────────────────────────────────────────────────

def test_force_overrides_the_cap_guard(flips):
    results = {2: result(saved=31500, complete=False, pre_active=21922)}
    refresh.stage_stale(FakeDB(), RUN_START, results, force=True)
    assert staled(flips, 2, "rent")


def test_force_does_not_resurrect_an_unscraped_source(flips):
    """--force overrides the quality guards, never the existence one:
    no cutoff makes staling a source you did not scrape correct."""
    results = {1: result(saved=5400, complete=True, pre_active=5422)}
    refresh.stage_stale(FakeDB(), RUN_START, results, force=True)
    assert not any(sid == 2 for sid, *_ in flips)


# ── resume mode ──────────────────────────────────────────────────────

def test_resume_mode_stales_everything(flips):
    """`--stale --stale-cutoff ...` passes no scrape results: the caller
    supplied a cutoff deliberately and owns the decision."""
    refresh.stage_stale(FakeDB(), RUN_START, None)
    for sid in (1, 2):
        for category in ("sale", "rent"):
            assert staled(flips, sid, category)


# ── ordering ─────────────────────────────────────────────────────────

def test_delist_runs_before_stale(flips):
    """stale→delisted must precede active→stale, or rows that just went
    stale in this same pass get delisted immediately."""
    results = {1: result(saved=5400, complete=True, pre_active=5422)}
    refresh.stage_stale(FakeDB(), RUN_START, results)
    sale = [c for c in flips if c[1] == "sale"]
    assert sale[0][2:] == ("stale", "delisted")
    assert sale[1][2:] == ("active", "stale")


# ── the scrapers' half of the contract ───────────────────────────────

def test_scrapers_report_completeness(monkeypatch):
    """run() must return (saved, complete) — stage_scrape unpacks two
    values and a bare int would raise.

    Note the exception carries a real 404 status. An exception with no
    status (a parse crash, say) is deliberately NOT treated as the end of
    the list — we only know we walked off the end if the server said so.
    """
    from scripts import scrape_property24, scrape_buyrentkenya

    for mod, name in ((scrape_property24, "P24"), (scrape_buyrentkenya, "BRK")):
        attr = "Property24Scraper" if name == "P24" else "BuyRentKenyaScraper"
        _scraper_raising(monkeypatch, mod, attr, _http_error(404))
        saved, complete = mod.run(max_pages=5, category="sale")
        assert saved == 0
        assert complete is True, f"{name}: a 404 streak IS the end of the list"


def test_unknown_exception_is_not_the_end_of_the_list(monkeypatch):
    """A parse crash or any error without an HTTP status proves nothing
    about pagination. Fail closed."""
    from scripts import scrape_property24 as p24
    _scraper_raising(monkeypatch, p24, "Property24Scraper",
                     ValueError("parser blew up"))
    _, complete = p24.run(max_pages=5, category="sale")
    assert complete is False


def test_multi_index_category_sums_and_ands(monkeypatch):
    """A category spans several index URLs (houses + apartments). Saved
    counts add; completeness must AND — one capped index means the
    category was not fully seen, however well the others went."""
    from scripts import scrape_property24 as p24

    calls = []

    def fake_crawl(base_url, cap, category):
        calls.append(base_url)
        # first index finishes cleanly, second hits the cap
        return (100, True) if len(calls) == 1 else (50, False)

    monkeypatch.setattr(p24, "_crawl_index", fake_crawl)
    monkeypatch.setattr(p24, "CATEGORY_URLS", {"sale": ["a", "b"]})
    saved, complete = p24.run(category="sale")
    assert saved == 150
    assert complete is False, "one capped index must sink the whole category"
    assert calls == ["a", "b"], "every index in the category must be crawled"


def test_multi_index_complete_only_when_all_complete(monkeypatch):
    from scripts import scrape_property24 as p24
    monkeypatch.setattr(p24, "_crawl_index",
                        lambda url, cap, cat: (10, True))
    monkeypatch.setattr(p24, "CATEGORY_URLS", {"sale": ["a", "b", "c"]})
    saved, complete = p24.run(category="sale")
    assert (saved, complete) == (30, True)


def test_brk_indexes_apartments_for_sale():
    """Closing half the sub-20M gap: BRK's whole corpus is 49 MB, so
    every category can still be crawled to completion with this added."""
    from scripts import scrape_buyrentkenya as brk
    assert any("flats-apartments-for-sale" in u
               for u in brk.CATEGORY_URLS["sale"])


def test_p24_sale_stays_completable():
    """P24 sale-houses is 536 pages and completes, so it stales. P24
    apartments-for-sale is 4,441 pages and never could — and since
    `complete` ANDs across a category's indexes, adding it would stop
    the houses staling too. Deliberately excluded until disk allows."""
    from scripts import scrape_property24 as p24
    assert p24.CATEGORY_URLS["sale"] == [
        "https://www.property24.co.ke/houses-for-sale-in-nairobi-p95"
    ], ("adding an uncrawlable index to P24 sale silently disables stale "
        "detection for the whole category — see ADR 0008 and probe_depth")


# ── 404 vs 503: what a failure streak actually means ─────────────────

def _scraper_raising(monkeypatch, mod, attr, exc):
    class Boom:
        def fetch(self, url):
            raise exc

        def parse(self, html):
            return []

    monkeypatch.setattr(mod, attr, lambda: Boom())
    monkeypatch.setattr(mod, "save_listings", lambda *a, **k: None)


def _http_error(code):
    import requests
    e = requests.HTTPError(f"{code} error")
    e.response = type("R", (), {"status_code": code})()
    return e


def test_404_streak_is_the_end_of_the_list(monkeypatch):
    """Both sites 404 past the last page — that IS how a full crawl ends."""
    from scripts import scrape_property24 as p24
    _scraper_raising(monkeypatch, p24, "Property24Scraper", _http_error(404))
    saved, complete = p24.run(max_pages=10, category="sale")
    assert (saved, complete) == (0, True)


def test_503_streak_is_not_the_end_of_the_list(monkeypatch):
    """2026-08-26: P24 threw 503s after 58 of 522 sale pages. The scraper
    reported complete=True and only MIN_SCRAPE_RATIO stopped 41,000 live
    listings being marked dead. A sick server tells you nothing about
    where the list ends."""
    from scripts import scrape_property24 as p24
    _scraper_raising(monkeypatch, p24, "Property24Scraper", _http_error(503))
    saved, complete = p24.run(max_pages=10, category="sale")
    assert saved == 0
    assert complete is False


def test_timeout_streak_is_not_the_end_of_the_list(monkeypatch):
    import requests
    from scripts import scrape_buyrentkenya as brk
    _scraper_raising(monkeypatch, brk, "BuyRentKenyaScraper",
                     requests.exceptions.ReadTimeout("timed out"))
    saved, complete = brk.run(max_pages=10, category="rent")
    assert complete is False


def test_one_5xx_poisons_an_otherwise_404_streak(monkeypatch):
    """Mixed streak: if any failure was a server error we did not
    establish the end of the list, however the others looked."""
    from scripts import scrape_property24 as p24
    errors = iter([_http_error(404), _http_error(503), _http_error(404)])

    class Mixed:
        def fetch(self, url):
            raise next(errors)

        def parse(self, html):
            return []

    monkeypatch.setattr(p24, "Property24Scraper", lambda: Mixed())
    monkeypatch.setattr(p24, "save_listings", lambda *a, **k: None)
    saved, complete = p24.run(max_pages=10, category="sale")
    assert complete is False


def test_a_success_resets_the_streak_verdict(monkeypatch):
    """A 503 early on must not taint a genuine 404 ending later."""
    from scripts import scrape_property24 as p24
    seq = iter([_http_error(503), None, None,
                _http_error(404), _http_error(404), _http_error(404)])

    class Flaky:
        def fetch(self, url):
            exc = next(seq)
            if exc:
                raise exc
            return "<html/>"

        def parse(self, html):
            return ["listing"]

    monkeypatch.setattr(p24, "Property24Scraper", lambda: Flaky())
    monkeypatch.setattr(p24, "save_listings", lambda *a, **k: None)
    saved, complete = p24.run(max_pages=10, category="sale")
    assert saved == 2
    assert complete is True


def test_hitting_the_cap_reports_incomplete(monkeypatch):
    """The case that caused the incident: every page returns listings, so
    the loop exits only because it ran out of cap."""
    from scripts import scrape_property24 as p24

    class EndlessScraper:
        def fetch(self, url):
            return "<html/>"

        def parse(self, html):
            return ["listing"]

    monkeypatch.setattr(p24, "Property24Scraper", lambda: EndlessScraper())
    monkeypatch.setattr(p24, "save_listings", lambda *a, **k: None)
    saved, complete = p24.run(max_pages=3, category="rent")
    assert saved == 3
    assert complete is False

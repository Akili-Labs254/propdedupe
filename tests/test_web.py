"""Web route tests — real HTTP requests against the app with a fake DB.

The fake Supabase client returns queued responses per table (popped in
call order), so tests exercise the actual FastAPI routing, form parsing,
template rendering, and redirect logic — the layers unit tests miss.
(The empty-string form bug that 422'd every search lived exactly there.)

Run: pytest tests/test_web.py
"""
import pytest
from fastapi.testclient import TestClient

import backend.web.routes as web_routes
import backend.web.segments as web_segments
import backend.web.admin as web_admin
import backend.api.routes.review as api_review
from backend.api.main import app


# ── fake supabase client ─────────────────────────────────────────────

class FakeResult:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count if count is not None else len(data)


class FakeQuery:
    """Chainable stand-in for postgrest's request builder."""

    def __init__(self, pop_response):
        self._pop = pop_response
        self.not_ = self  # `.not_.is_(...)` chains back to us

    def __getattr__(self, name):
        # select/eq/gte/lte/ilike/is_/in_/order/range/limit/... → chain
        def method(*args, **kwargs):
            return self
        return method

    def execute(self):
        return self._pop()


class FakeClient:
    """responses: {table_name: [FakeResult, ...]} popped in call order."""

    def __init__(self, responses):
        self._responses = {k: list(v) for k, v in responses.items()}

    def table(self, name):
        def pop():
            queue = self._responses.get(name)
            assert queue, f"unexpected query on table '{name}'"
            return queue.pop(0)
        return FakeQuery(pop)

    def rpc(self, *args, **kwargs):
        return FakeQuery(lambda: FakeResult([]))


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


def use_db(monkeypatch, responses):
    db = FakeClient(responses)
    for mod in (web_routes, web_segments, web_admin, api_review):
        monkeypatch.setattr(mod, "get_client", lambda db=db: db)
    return db


PROP = {
    "id": 7, "title": "5 Bed Villa in Karen", "slug": "5-bed-villa-abc123",
    "price": 85000000, "price_min": 84000000, "price_max": 85000000,
    "location": "Karen", "suburb": "karen", "bedrooms": 5, "bathrooms": 4.0,
    "size_sqm": 400.0, "image_url": None, "source_count": 2,
    "status": "active",
}
STATS = {"suburb": "kilimani", "bedrooms": None, "is_suburb_total": True,
         "property_count": 42, "low_price": 8000000, "high_price": 120000000,
         "median_price": 35000000.0}


# ── home / search ────────────────────────────────────────────────────

def test_home_renders(client, monkeypatch):
    use_db(monkeypatch, {"properties": [FakeResult([PROP], count=1)],
                         "suburb_stats": [FakeResult([STATS])]})
    r = client.get("/")
    assert r.status_code == 200
    assert "5 Bed Villa" in r.text
    assert "84,000,000" in r.text          # price range shown
    assert "/houses-for-sale/kilimani" in r.text  # suburb crawl path


def test_search_with_empty_form_fields(client, monkeypatch):
    """Regression: HTML forms submit '' for blank numeric fields; this
    used to 422 every search."""
    use_db(monkeypatch, {"properties": [FakeResult([], count=0)],
                         "suburb_stats": [FakeResult([])]})
    r = client.get("/?location=kilimani&bedrooms=&min_price=&max_price=")
    assert r.status_code == 200
    assert "No properties match" in r.text


def test_search_with_junk_numbers(client, monkeypatch):
    use_db(monkeypatch, {"properties": [FakeResult([], count=0)],
                         "suburb_stats": [FakeResult([])]})
    assert client.get("/?bedrooms=abc&min_price=-5").status_code == 200


# ── property pages ───────────────────────────────────────────────────

def test_property_page(client, monkeypatch):
    use_db(monkeypatch, {
        "properties": [FakeResult([PROP])],
        "listings": [FakeResult([{"source_id": 1, "url": "https://brk/x",
                                  "title": "t", "price": 85000000,
                                  "status": "active", "last_seen": "2026"}])],
    })
    r = client.get("/property/5-bed-villa-abc123")
    assert r.status_code == 200
    assert "BuyRentKenya" in r.text
    assert "application/ld+json" in r.text


def test_outbound_click_is_instrumented(client, monkeypatch):
    """The click-through to a source listing is the closest thing to a
    conversion this site has, and it was invisible until 2026-08."""
    use_db(monkeypatch, {
        "properties": [FakeResult([PROP])],
        "listings": [FakeResult([{"source_id": 2, "url": "https://p24/x",
                                  "title": "t", "price": 85000000,
                                  "status": "active", "last_seen": "2026"}])],
    })
    r = client.get("/property/5-bed-villa-abc123")
    assert r.status_code == 200
    assert 'data-source="property24"' in r.text     # link is tagged
    assert 'a.source-link' in r.text                # listener is present
    assert '"out/"' in r.text or "out/" in r.text   # event path


def test_retired_slug_redirects_301(client, monkeypatch):
    use_db(monkeypatch, {
        "properties": [FakeResult([]),                    # canonical miss
                       FakeResult([{"id": 7}])],          # target exists
        "property_slugs": [FakeResult([{"target_slug": "new-slug"}])],
    })
    r = client.get("/property/old-slug", follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"] == "/property/new-slug"


def test_unknown_slug_404(client, monkeypatch):
    use_db(monkeypatch, {"properties": [FakeResult([])],
                         "property_slugs": [FakeResult([])]})
    assert client.get("/property/nope").status_code == 404


# ── segment pages ────────────────────────────────────────────────────

def test_segment_page(client, monkeypatch):
    use_db(monkeypatch, {
        "suburb_stats": [FakeResult([STATS]),      # stats
                         FakeResult([{"bedrooms": 3, "property_count": 11}]),
                         FakeResult([{"suburb": "lavington",
                                      "property_count": 30}])],  # neighbours
        "properties": [FakeResult([PROP], count=42)],
    })
    r = client.get("/houses-for-sale/kilimani")
    assert r.status_code == 200
    # "Homes", not "Houses": the URL prefix is frozen for SEO but the copy
    # had to change once apartments-for-sale entered the corpus. The page
    # must not claim to list houses while showing flats.
    assert "Homes for Sale in Kilimani" in r.text
    assert "median KSh 35,000,000" in r.text
    assert "/houses-for-sale/kilimani/3-bedroom" in r.text
    assert "Lavington" in r.text               # adjacency link mesh


def test_suburb_slug_roundtrip():
    """Multi-word suburbs must survive DB value → URL → DB value."""
    for name in ("kilimani", "ngong road", "spring valley", "south b",
                 "general mathenge drive", "nairobi cbd"):
        assert " " not in web_routes.suburb_slug(name)
        assert web_routes.suburb_from_slug(web_routes.suburb_slug(name)) == name


def test_suburb_from_slug_accepts_legacy_and_case():
    assert web_routes.suburb_from_slug("ngong road") == "ngong road"
    assert web_routes.suburb_from_slug("Ngong-Road") == "ngong road"


def test_multiword_segment_links_have_no_spaces(client, monkeypatch):
    """Regression: link builders dropped the raw suburb value into the
    path, producing '/houses-for-sale/ngong road' — which Google indexed."""
    stats = dict(STATS, suburb="ngong road")
    use_db(monkeypatch, {
        "suburb_stats": [FakeResult([stats]),
                         FakeResult([{"bedrooms": 3, "property_count": 11}]),
                         FakeResult([{"suburb": "spring valley",
                                      "property_count": 30}])],
        "properties": [FakeResult([PROP], count=42)],
    })
    r = client.get("/houses-for-sale/ngong-road")
    assert r.status_code == 200
    assert "/houses-for-sale/ngong-road/3-bedroom" in r.text   # bed links
    assert "/houses-for-sale/spring-valley" in r.text          # neighbours
    assert "houses-for-sale/ngong road" not in r.text          # canonical
    assert "%20" not in r.text


def test_space_form_301s_to_slug(client, monkeypatch):
    """The URLs already in Search Console must redirect, not 404."""
    use_db(monkeypatch, {})
    r = client.get("/houses-for-sale/kiambu road", follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"] == "/houses-for-sale/kiambu-road"

    r = client.get("/apartments-for-rent/spring valley/2-bedroom?page=3",
                   follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"] == \
        "/apartments-for-rent/spring-valley/2-bedroom?page=3"


def test_mixed_case_301s_to_slug(client, monkeypatch):
    use_db(monkeypatch, {})
    r = client.get("/houses-for-sale/Karen", follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"] == "/houses-for-sale/karen"


def test_punctuated_suburb_resolves_via_cache(client, monkeypatch):
    """A suburb whose slug isn't a plain space-swap ('st.mary road')
    falls back to the cached slug→suburb map instead of 404ing."""
    web_segments._SLUG_CACHE.clear()
    stats = dict(STATS, suburb="st.mary road")
    use_db(monkeypatch, {
        "suburb_stats": [FakeResult([]),                       # direct miss
                         FakeResult([{"suburb": "st.mary road"}]),  # map
                         FakeResult([stats]),                  # retry hit
                         FakeResult([]),                       # bed links
                         FakeResult([])],                      # neighbours
        "properties": [FakeResult([PROP], count=42)],
    })
    r = client.get("/houses-for-sale/st-mary-road")
    assert r.status_code == 200
    web_segments._SLUG_CACHE.clear()


def test_segment_below_threshold_serves_but_noindex(client, monkeypatch):
    """Regression: thin pages used to 404. Inventory crosses MIN_SEGMENT
    constantly, so a page Google indexed at 5 properties 404'd at 4 — and
    our best search click landed on exactly that (city-park-drive, 2026-08).
    Now it serves with noindex."""
    use_db(monkeypatch, {
        "suburb_stats": [FakeResult([dict(STATS, property_count=3)]),
                         FakeResult([]),      # bed links
                         FakeResult([])],     # neighbours
        "properties": [FakeResult([PROP], count=3)],
    })
    r = client.get("/houses-for-sale/kilimani")
    assert r.status_code == 200
    assert 'name="robots" content="noindex' in r.text


def test_healthy_segment_is_indexable(client, monkeypatch):
    use_db(monkeypatch, {
        "suburb_stats": [FakeResult([STATS]), FakeResult([]), FakeResult([])],
        "properties": [FakeResult([PROP], count=42)],
    })
    r = client.get("/houses-for-sale/kilimani")
    assert r.status_code == 200
    assert "noindex" not in r.text


def test_unknown_suburb_still_404s(client, monkeypatch):
    """No stats row at all — the suburb doesn't exist. Must still 404,
    or every typo becomes a soft-200."""
    web_segments._SLUG_CACHE.clear()
    use_db(monkeypatch, {
        "suburb_stats": [FakeResult([]),      # direct miss
                         FakeResult([])],     # slug→suburb map, empty
    })
    assert client.get("/houses-for-sale/atlantis").status_code == 404
    web_segments._SLUG_CACHE.clear()


def test_zero_count_404s(client, monkeypatch):
    """Defensive: a stats row with property_count 0 is not a page."""
    web_segments._SLUG_CACHE.clear()
    use_db(monkeypatch, {
        "suburb_stats": [FakeResult([dict(STATS, property_count=0)]),
                         FakeResult([])],
    })
    assert client.get("/houses-for-sale/kilimani").status_code == 404
    web_segments._SLUG_CACHE.clear()


def test_bad_beds_segment_404(client, monkeypatch):
    use_db(monkeypatch, {})
    assert client.get("/houses-for-sale/kilimani/99-bedroom").status_code == 404
    assert client.get("/houses-for-sale/kilimani/blah").status_code == 404


# ── sitemap / robots ─────────────────────────────────────────────────

def test_sitemap(client, monkeypatch):
    use_db(monkeypatch, {
        # sale: totals + beds rows; rent: no totals → category skipped
        "suburb_stats": [FakeResult([STATS, {"suburb": "ngong road"}]),
                         FakeResult([{"suburb": "ngong road", "bedrooms": 3}]),
                         FakeResult([])],
        "properties": [FakeResult([{"slug": "abc-123"}])],
    })
    r = client.get("/sitemap.xml")
    assert r.status_code == 200
    assert "<loc>" in r.text
    assert "/houses-for-sale/kilimani</loc>" in r.text
    assert "/property/abc-123</loc>" in r.text
    # the sitemap is how Google found the space URLs in the first place
    assert "/houses-for-sale/ngong-road</loc>" in r.text
    assert "/houses-for-sale/ngong-road/3-bedroom</loc>" in r.text
    assert "ngong road" not in r.text


def test_sitemap_restricts_properties_to_multi_source(client, monkeypatch):
    """Crawl budget on a new domain is scarce: the sitemap carries segment
    pages plus only multi-source properties, not all ~28k detail pages.
    (GSC 2026-07-29: 1.13K indexed vs 28,249 discovered-not-indexed.)"""
    assert web_segments.SITEMAP_MULTI_SOURCE_ONLY is True

    captured = {}

    class RecordingQuery(FakeQuery):
        def gt(self, col, val):
            captured[col] = val
            return self

    real_table = FakeClient.table

    use_db(monkeypatch, {
        "suburb_stats": [FakeResult([STATS]), FakeResult([]), FakeResult([])],
        "properties": [FakeResult([{"slug": "abc-123"}])],
    })
    monkeypatch.setattr(
        FakeClient, "table",
        lambda self, name: (RecordingQuery(real_table(self, name)._pop)
                            if name == "properties" else real_table(self, name)))

    assert client.get("/sitemap.xml").status_code == 200
    assert captured.get("source_count") == 1   # .gt("source_count", 1)


# ── homepage indexing rules ──────────────────────────────────────────

def _home(client, monkeypatch, query="/"):
    use_db(monkeypatch, {
        "properties": [FakeResult([PROP], count=200)],
        "listings": [FakeResult([], count=0), FakeResult([], count=0)],
        "suburb_stats": [FakeResult([])],
    })
    r = client.get(query)
    assert r.status_code == 200
    return r.text


def test_bare_homepage_is_indexable(client, monkeypatch):
    body = _home(client, monkeypatch, "/")
    assert 'name="robots"' not in body
    assert 'rel="canonical"' in body


def test_rent_root_is_indexable(client, monkeypatch):
    """Both category roots are real pages, not search results."""
    body = _home(client, monkeypatch, "/?category=rent")
    assert 'name="robots"' not in body
    assert "/?category=rent" in body


def test_paginated_homepage_is_noindexed(client, monkeypatch):
    """Google indexed /?page=8&category=sale&location= and spent crawl
    budget there instead of on the property pages earning the clicks."""
    body = _home(client, monkeypatch, "/?page=8")
    assert 'content="noindex,follow"' in body


def test_filtered_homepage_is_noindexed(client, monkeypatch):
    """Filter combinations are effectively infinite and near-duplicate."""
    for q in ("/?location=Kilimani", "/?bedrooms=2", "/?max_price=9000000",
              "/?sort=price_asc"):
        assert 'content="noindex,follow"' in _home(client, monkeypatch, q), q


def test_noindex_still_follows(client, monkeypatch):
    """nofollow would strand the property pages behind a filtered view."""
    body = _home(client, monkeypatch, "/?bedrooms=3")
    assert "noindex,follow" in body
    assert "nofollow" not in body


def test_filtered_views_canonicalise_to_the_category_root(client, monkeypatch):
    body = _home(client, monkeypatch, "/?category=sale&location=Karen&page=3")
    assert 'rel="canonical"' in body
    assert "location=Karen" not in body.split('rel="canonical"')[1][:120]


# ── listing age badge ────────────────────────────────────────────────

def test_days_listed_handles_every_shape_postgrest_returns():
    """Supabase hands back ISO strings, sometimes Z-suffixed, sometimes
    naive. A crash here would 500 every card on the site."""
    from datetime import datetime, timedelta, timezone
    from backend.web.routes import days_listed
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=12)

    assert days_listed(old.isoformat()) == 12
    assert days_listed(old.isoformat().replace("+00:00", "Z")) == 12
    assert days_listed(old.replace(tzinfo=None).isoformat()) == 12
    assert days_listed(now.isoformat()) == 0


def test_days_listed_refuses_junk_rather_than_raising():
    from backend.web.routes import days_listed
    for junk in (None, "", "not-a-date", 12345):
        assert days_listed(junk) is None, f"{junk!r} should be None"


def test_days_listed_never_returns_negative():
    """Clock skew or a future first_seen must not render 'seen for -1
    days' — return None and let the template hide the badge."""
    from datetime import datetime, timedelta, timezone
    from backend.web.routes import days_listed
    future = datetime.now(timezone.utc) + timedelta(days=3)
    assert days_listed(future.isoformat()) is None


# ── sorting ──────────────────────────────────────────────────────────

class SortRecorder:
    """Wraps a FakeQuery, recording .order() and .is_() calls."""

    def __init__(self, inner, log):
        self._inner, self._log = inner, log
        self.not_ = self

    def order(self, col, desc=False):
        self._log.append(("order", col, desc))
        return self

    def is_(self, col, val):
        self._log.append(("is_", col, val))
        return self

    def __getattr__(self, name):
        return lambda *a, **k: self

    def execute(self):
        return self._inner.execute()


def _sorted_by(client, monkeypatch, query):
    log = []
    real_table = FakeClient.table
    use_db(monkeypatch, {
        "properties": [FakeResult([PROP], count=1)],
        "listings": [FakeResult([], count=0), FakeResult([], count=0)],
        "suburb_stats": [FakeResult([])],
    })
    monkeypatch.setattr(
        FakeClient, "table",
        lambda self, name: (SortRecorder(real_table(self, name), log)
                            if name == "properties" else real_table(self, name)))
    r = client.get(query)
    assert r.status_code == 200
    return log


def test_default_sort_is_newest(client, monkeypatch):
    log = _sorted_by(client, monkeypatch, "/")
    assert ("order", "first_seen", True) in log


def test_price_ascending(client, monkeypatch):
    log = _sorted_by(client, monkeypatch, "/?sort=price_asc")
    assert ("order", "price", False) in log


def test_price_descending(client, monkeypatch):
    log = _sorted_by(client, monkeypatch, "/?sort=price_desc")
    assert ("order", "price", True) in log


def test_price_sort_excludes_poa(client, monkeypatch):
    """Sorting by price with price IS NULL rows in the set fills page 1
    with properties showing no price. Someone who asked to sort by price
    wants prices."""
    log = _sorted_by(client, monkeypatch, "/?sort=price_asc")
    assert ("is_", "price", "null") in log


def test_newest_sort_keeps_poa(client, monkeypatch):
    log = _sorted_by(client, monkeypatch, "/")
    assert ("is_", "price", "null") not in log


def test_unknown_sort_falls_back_rather_than_500(client, monkeypatch):
    """?sort=; DROP TABLE and friends must not reach the query builder."""
    log = _sorted_by(client, monkeypatch, "/?sort=nonsense")
    ordered_cols = [col for kind, col, _ in log if kind == "order"]
    assert ordered_cols == ["first_seen", "id"], (
        f"unknown sort must fall back to the default, got {ordered_cols}")


def test_sort_survives_pagination(client, monkeypatch):
    """The pagination links are built in the template; dropping sort
    there would silently reset the order on page 2."""
    use_db(monkeypatch, {
        "properties": [FakeResult([PROP], count=200)],
        "listings": [FakeResult([], count=0), FakeResult([], count=0)],
        "suburb_stats": [FakeResult([])],
    })
    body = client.get("/?sort=price_asc").text
    assert "sort=price_asc" in body


# ── vanity CTA redirects ─────────────────────────────────────────────

def test_vanity_redirects_with_ref(client, monkeypatch):
    """TikTok strips referrers and viewers type the URL, so the path
    itself has to carry the attribution."""
    use_db(monkeypatch, {})
    r = client.get("/50k", follow_redirects=False)
    assert r.status_code == 302          # 302 not 301 — destination may change
    assert "ref=tt-50k" in r.headers["location"]
    assert r.headers["location"].startswith("/?category=rent")


def test_vanity_appends_ref_correctly_without_query(client, monkeypatch):
    """A destination with no existing query string needs '?' not '&'."""
    use_db(monkeypatch, {})
    r = client.get("/karen", follow_redirects=False)
    assert r.headers["location"] == \
        "/apartments-for-rent/karen/3-bedroom?ref=tt-karen"


def test_vanity_is_case_insensitive(client, monkeypatch):
    """People type what they remember, not what you wrote."""
    use_db(monkeypatch, {})
    assert client.get("/50K", follow_redirects=False).status_code == 302


def test_unknown_vanity_code_404s(client, monkeypatch):
    use_db(monkeypatch, {})
    assert client.get("/nonsense", follow_redirects=False).status_code == 404


def test_every_vanity_code_resolves_and_has_a_unique_ref(client, monkeypatch):
    """A code is added per video, by hand, usually in a hurry the night
    before posting. Two codes sharing a ref tag would silently merge two
    videos' traffic in GoatCounter and the mistake would be invisible —
    the numbers would just be quietly wrong.
    """
    from backend.web.vanity import VANITY, destination
    use_db(monkeypatch, {})
    refs = [ref for _, ref in VANITY.values()]
    assert len(refs) == len(set(refs)), f"duplicate ref tag in VANITY: {refs}"
    for code in VANITY:
        assert code == code.lower(), f"{code} must be lowercase to be typeable"
        r = client.get(f"/{code}", follow_redirects=False)
        assert r.status_code == 302, code
        assert r.headers["location"] == destination(code)
        assert "ref=" in r.headers["location"], code


def test_vanity_does_not_shadow_real_routes(client, monkeypatch):
    """The catch-all is registered last. If that ever changes, these
    top-level routes get swallowed and the site breaks silently."""
    use_db(monkeypatch, {})
    r = client.get("/robots.txt")
    assert r.status_code == 200 and "Sitemap:" in r.text

    use_db(monkeypatch, {
        "suburb_stats": [FakeResult([STATS]), FakeResult([]), FakeResult([])],
        "properties": [FakeResult([PROP], count=42)],
    })
    assert client.get("/houses-for-sale/kilimani").status_code == 200


def test_robots(client, monkeypatch):
    use_db(monkeypatch, {})
    r = client.get("/robots.txt")
    assert r.status_code == 200
    assert "Sitemap:" in r.text
    assert "Disallow: /admin" in r.text


# ── admin review UI ──────────────────────────────────────────────────

def test_admin_requires_key(client, monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", "sekrit")
    use_db(monkeypatch, {})
    assert client.get("/admin/review").status_code == 401
    assert client.get("/admin/review?key=wrong").status_code == 401


def test_admin_renders_queue(client, monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", "sekrit")
    listing = {"id": 1, "source_id": 2, "title": "House A",
               "price": 350000000, "location": "Runda", "bedrooms": 5,
               "baths": 6.0, "image_url": None, "url": "https://p24/x",
               "status": "active"}
    match = {"id": 9, "listing_a_id": 1, "listing_b_id": 2, "score": 0.12,
             "tier": "review", "scope": "intra", "image_score": 0.0,
             "price_score": 0.0, "beds_score": 0.0, "baths_score": 0.0,
             "location_score": 0.0}
    use_db(monkeypatch, {
        "matches": [FakeResult([match], count=1)],
        "listings": [FakeResult([listing, dict(listing, id=2)])],
    })
    r = client.get("/admin/review?key=sekrit&scope=intra")
    assert r.status_code == 200
    assert "House A" in r.text
    assert 'name="decision_1_2"' in r.text        # batch radios, pair-keyed
    assert "/admin/review/batch" in r.text        # one form, one confirm


def test_admin_batch_apply(client, monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", "sekrit")
    # dismiss path: fetch match (review tier) → update decision → update tier
    match = {"id": 9, "listing_a_id": 1, "listing_b_id": 2, "tier": "review"}
    use_db(monkeypatch, {
        # pair lookup → match fetch → tier update (+ decision upsert)
        "matches": [FakeResult([{"id": 9}]), FakeResult([match]),
                    FakeResult([match])],
        "review_decisions": [FakeResult([])],
    })
    r = client.post("/admin/review/batch", data={
        "key": "sekrit", "scope": "intra", "category": "rent", "page": "2",
        "decision_1_2": "dismiss", "decision_3_4": "skip",
    }, follow_redirects=False)
    assert r.status_code == 303
    loc = r.headers["location"]
    assert "applied=1" in loc and "failed=0" in loc
    assert "scope=intra" in loc and "category=rent" in loc and "page=2" in loc


def test_admin_batch_requires_key(client, monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", "sekrit")
    use_db(monkeypatch, {})
    r = client.post("/admin/review/batch", data={"key": "wrong"},
                    follow_redirects=False)
    assert r.status_code == 401

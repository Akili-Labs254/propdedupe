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
    assert "Houses for Sale in Kilimani" in r.text
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


def test_segment_below_threshold_404(client, monkeypatch):
    use_db(monkeypatch, {
        "suburb_stats": [FakeResult([dict(STATS, property_count=3)])]})
    assert client.get("/houses-for-sale/kilimani").status_code == 404


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

"""Incremental hash stage: what counts as "this image is gone".

Marking a listing unfetchable (image_phash = '') is permanent: the
incremental query only picks up NULL hashes, so the listing drops out of
image matching for good. Before 24 Nov 2026 ANY failure did that,
including a timeout or a dropped connection, so a wifi blip during a long
hash stage (26,000 images after the November image incident) would have
silently removed hundreds of listings from duplicate detection.
"""
import pytest

import scripts.backfill_all_hashes as bah
import scripts.refresh as refresh


class FakeListings:
    """Just enough of the postgrest chain for stage_hashes."""

    def __init__(self, rows):
        self.rows = {r["id"]: dict(r) for r in rows}
        self._gt = 0
        self._limit = 100
        self._update = None
        self._in = None

    # read chain
    def table(self, name):
        self._gt, self._update, self._in = 0, None, None
        return self

    def select(self, *a, **k):
        return self

    @property
    def not_(self):
        return self

    def is_(self, *a):
        return self

    def order(self, *a, **k):
        return self

    def gt(self, col, value):
        self._gt = value
        return self

    def limit(self, n):
        self._limit = n
        return self

    # write chain
    def update(self, values):
        self._update = values
        return self

    def in_(self, col, ids):
        self._in = ids
        return self

    def execute(self):
        from types import SimpleNamespace
        if self._update is not None:
            for i in self._in:
                self.rows[i].update(self._update)
            return SimpleNamespace(data=[])
        pending = sorted(
            (r for r in self.rows.values()
             if r["image_url"] and r.get("image_phash") is None
             and r["id"] > self._gt),
            key=lambda r: r["id"])[: self._limit]
        return SimpleNamespace(data=[{"id": r["id"], "image_url": r["image_url"]}
                                     for r in pending])


GOOD = {"image_phash": "a" * 16, "image_ahash": "b" * 16, "image_whash": "c" * 16}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    import time
    monkeypatch.setattr(time, "sleep", lambda s: None)


def rows(n):
    return [{"id": i, "image_url": f"http://img/{i}", "image_phash": None}
            for i in range(1, n + 1)]


def test_transient_failure_is_not_marked_unfetchable(monkeypatch):
    def compute(url):
        if url.endswith("/2"):
            raise bah.TransientImageError("ConnectTimeout")
        return GOOD
    monkeypatch.setattr(bah, "compute_hashes", compute)
    db = FakeListings(rows(3))
    refresh.stage_hashes(db)
    assert db.rows[2]["image_phash"] is None          # left for next run
    assert db.rows[1]["image_phash"] == GOOD["image_phash"]
    assert db.rows[3]["image_phash"] == GOOD["image_phash"]


def test_gone_image_is_marked_unfetchable(monkeypatch):
    def compute(url):
        raise OSError("cannot identify image file")
    monkeypatch.setattr(bah, "compute_hashes", compute)
    db = FakeListings(rows(2))
    refresh.stage_hashes(db)
    assert db.rows[1]["image_phash"] == ""


def test_network_down_stops_the_stage_early(monkeypatch):
    calls = {"n": 0}

    def compute(url):
        calls["n"] += 1
        raise bah.TransientImageError("Failed to resolve")
    monkeypatch.setattr(bah, "compute_hashes", compute)
    db = FakeListings(rows(300))
    refresh.stage_hashes(db)
    assert calls["n"] == refresh.MAX_TRANSIENT_STREAK
    assert all(r["image_phash"] is None for r in db.rows.values())


def test_stuck_image_is_tried_once_per_run(monkeypatch):
    """Keyset pass: a row that keeps failing transiently must not be
    retried at the head of every later batch."""
    tried = []

    def compute(url):
        tried.append(url)
        if url.endswith("/1"):
            raise bah.TransientImageError("HTTP 503")
        return GOOD
    monkeypatch.setattr(bah, "compute_hashes", compute)
    db = FakeListings(rows(250))
    refresh.stage_hashes(db)
    assert tried.count("http://img/1") == 1
    assert sum(1 for r in db.rows.values() if r["image_phash"]) == 249


def test_compute_hashes_classifies_http_errors(monkeypatch):
    class R:
        def __init__(self, code):
            self.status_code = code
            self.content = b""

        def raise_for_status(self):
            import requests
            if self.status_code >= 400:
                raise requests.HTTPError(str(self.status_code))

    for code in (429, 500, 503):
        monkeypatch.setattr(bah.requests, "get", lambda *a, code=code, **k: R(code))
        with pytest.raises(bah.TransientImageError):
            bah.compute_hashes("http://img/x")

    import requests
    monkeypatch.setattr(bah.requests, "get", lambda *a, **k: R(404))
    with pytest.raises(requests.HTTPError):
        bah.compute_hashes("http://img/x")

    def boom(*a, **k):
        raise requests.ConnectionError("Failed to resolve")
    monkeypatch.setattr(bah.requests, "get", boom)
    with pytest.raises(bah.TransientImageError):
        bah.compute_hashes("http://img/x")


def test_image_requests_identify_honestly(monkeypatch):
    seen = {}

    def get(url, timeout=None, headers=None):
        seen.update(headers or {})
        raise bah.requests.ConnectionError("stop here")
    monkeypatch.setattr(bah.requests, "get", get)
    with pytest.raises(bah.TransientImageError):
        bah.compute_hashes("http://img/x")
    assert seen.get("User-Agent") == bah.config.USER_AGENT

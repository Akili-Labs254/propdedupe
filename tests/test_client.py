"""Supabase client caching.

Every web request calls get_client(). Un-cached, each one built a fresh
client with its own httpx connection pool and SSL context, none of which
were ever closed — they accumulated until the 512MB Render instance OOM'd.
Three crashes on 2026-08-07, roughly 7-8 hours apart, accelerating as
traffic grew.

Run: pytest tests/test_client.py
"""
import backend.db.client as client_mod
from backend.db.client import get_client


class FakeClient:
    """Stands in for a real Supabase client so no network is touched."""
    _n = 0

    def __init__(self, *a, **kw):
        FakeClient._n += 1
        self.serial = FakeClient._n
        self.args = a
        self.kwargs = kw


def _patch(monkeypatch):
    FakeClient._n = 0
    get_client.cache_clear()
    monkeypatch.setattr(client_mod, "create_client",
                        lambda url, key, options=None: FakeClient(url, key))


def test_same_args_reuse_one_client(monkeypatch):
    _patch(monkeypatch)
    a, b, c = get_client(), get_client(), get_client()
    assert a is b is c
    assert FakeClient._n == 1, "a client per call is what OOM'd the box"


def test_different_timeouts_get_separate_clients(monkeypatch):
    """Web uses 120s, the batch pipeline 600s — they must not share."""
    _patch(monkeypatch)
    web = get_client(timeout=120)
    batch = get_client(timeout=600)
    assert web is not batch
    assert FakeClient._n == 2


def test_service_role_and_anon_are_separate(monkeypatch):
    """Different keys must never collapse into one cached client."""
    _patch(monkeypatch)
    svc = get_client(use_service_role=True)
    anon = get_client(use_service_role=False)
    assert svc is not anon
    assert svc.args[1] != anon.args[1]


def test_cache_is_bounded(monkeypatch):
    """maxsize caps the cache so odd timeouts can't grow it unboundedly."""
    _patch(monkeypatch)
    for t in range(100, 120):
        get_client(timeout=t)
    assert get_client.cache_info().currsize <= 4

"""with_retry: retries transient network/gateway errors, never real ones."""
import httpx
import pytest
from postgrest.exceptions import APIError

import backend.db.client as client


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(client.time, "sleep", lambda s: None)


def _api_error(code):
    return APIError({"message": "x", "code": code, "hint": "", "details": ""})


def _flaky(exc, fail_times):
    calls = {"n": 0}

    def call():
        calls["n"] += 1
        if calls["n"] <= fail_times:
            raise exc
        return "ok"
    return call, calls


@pytest.mark.parametrize("code", [502, "502", 503, 504])
def test_gateway_errors_are_retried(code):
    # 2026-11-03: a Cloudflare 502 arrived as APIError and killed the
    # P24 sale scrape because only httpx errors were retried.
    call, calls = _flaky(_api_error(code), 2)
    assert client.with_retry(call) == "ok"
    assert calls["n"] == 3


def test_network_errors_are_retried():
    call, calls = _flaky(httpx.ConnectError("boom"), 1)
    assert client.with_retry(call) == "ok"
    assert calls["n"] == 2


def test_real_errors_are_not_retried():
    call, calls = _flaky(_api_error("23505"), 1)
    with pytest.raises(APIError):
        client.with_retry(call)
    assert calls["n"] == 1


def test_gives_up_after_attempts():
    call, calls = _flaky(_api_error(502), 10)
    with pytest.raises(APIError):
        client.with_retry(call, attempts=3)
    assert calls["n"] == 3

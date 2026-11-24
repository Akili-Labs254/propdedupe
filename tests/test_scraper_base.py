"""Tests for is_end_of_pagination: the single decision that authorises
stale detection to mark listings as removed.

This function has now been wrong three times, in three different ways, and
each time MIN_SCRAPE_RATIO was the only thing that prevented a mass
false-delisting:

  2026-08-12  A capped crawl was treated as complete.       (ADR 0008)
  2026-08-26  A 503 streak was treated as complete.         (ADR 0008 amend)
  2026-09-02  A 403 streak was treated as complete.         (this file)

The third one is the reason these tests exist. The ADR's table said 404/410;
the code said `400 <= status < 500`. Nobody noticed the disagreement because
nothing exercised it: until BuyRentKenya started refusing GitHub's runners
and all three indexes reported a complete crawl of zero listings.

The rule, stated once: **refusal is not absence.** Only "there is no such
page" ends a crawl. Everything else fails closed.
"""

import pytest
import requests

from backend.scraper.base import END_OF_LIST_STATUSES, is_end_of_pagination


def http_error(status: int) -> requests.HTTPError:
    """An HTTPError shaped like the ones raise_for_status() produces."""
    response = requests.Response()
    response.status_code = status
    return requests.HTTPError(f"{status} Error", response=response)


@pytest.mark.parametrize("status", [404, 410])
def test_absence_ends_the_crawl(status):
    assert is_end_of_pagination(http_error(status)) is True


@pytest.mark.parametrize("status", [400, 401, 403, 405, 418, 422, 429, 451])
def test_refusal_does_not_end_the_crawl(status):
    """403 is the one that bit us: the page exists, we were refused it.
    Reading that as 'past the last page' authorises staling the corpus."""
    assert is_end_of_pagination(http_error(status)) is False


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_server_errors_do_not_end_the_crawl(status):
    assert is_end_of_pagination(http_error(status)) is False


def test_transport_failures_fail_closed():
    """No HTTP status at all: a timeout, a DNS failure, a reset. We learned
    nothing about where the list ends."""
    assert is_end_of_pagination(requests.Timeout("read timed out")) is False
    assert is_end_of_pagination(requests.ConnectionError("reset")) is False
    assert is_end_of_pagination(ValueError("unrelated")) is False


def test_exception_with_response_but_no_status():
    class Bare:
        pass

    exc = requests.HTTPError("weird")
    exc.response = Bare()
    assert is_end_of_pagination(exc) is False


def test_end_of_list_statuses_is_explicit_and_small():
    """A guard on the guard. Widening this set is a decision that needs a
    reason in the ADR, not a quiet boundary change: the last time it was a
    range, it silently included every 4xx."""
    assert END_OF_LIST_STATUSES == frozenset({404, 410})


# ── fetch(): riding out a short network drop ────────────────────────────
#
# 24 Nov 2026: the laptop lid closed mid-crawl, DNS failed for a few
# seconds, and three quick failures ended the rent crawl at page 861 of
# 1,500. fetch() retried 429/5xx with backoff but not network errors.

import backend.scraper.base as base_mod
from backend.scraper.base import BaseScraper


class _Resp:
    def __init__(self, status, text="ok"):
        self.status_code = status
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            r = requests.Response()
            r.status_code = self.status_code
            raise requests.HTTPError(f"{self.status_code}", response=r)


class _Session:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0
        self.headers = {}

    def get(self, url, timeout=None):
        self.calls += 1
        o = self.outcomes.pop(0)
        if isinstance(o, Exception):
            raise o
        return o


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(base_mod.time, "sleep", lambda s: None)


def test_dns_blip_is_ridden_out(no_sleep):
    s = _Session([requests.ConnectionError("Failed to resolve"),
                  requests.ConnectionError("Failed to resolve"),
                  _Resp(200, "page")])
    assert BaseScraper(session=s).fetch("http://x") == "page"
    assert s.calls == 3


def test_timeout_is_retried(no_sleep):
    s = _Session([requests.Timeout("read timed out"), _Resp(200, "page")])
    assert BaseScraper(session=s).fetch("http://x") == "page"


def test_long_outage_still_fails_and_fails_closed(no_sleep):
    err = requests.ConnectionError("Failed to resolve")
    s = _Session([err, err, err, err])
    with pytest.raises(requests.ConnectionError) as info:
        BaseScraper(session=s).fetch("http://x")
    assert is_end_of_pagination(info.value) is False
    assert s.calls == 4


def test_404_is_not_retried(no_sleep):
    s = _Session([_Resp(404)])
    with pytest.raises(requests.HTTPError):
        BaseScraper(session=s).fetch("http://x")
    assert s.calls == 1


def test_503_then_network_error_then_success(no_sleep):
    s = _Session([_Resp(503), requests.ConnectionError("reset"), _Resp(200, "ok")])
    assert BaseScraper(session=s).fetch("http://x") == "ok"

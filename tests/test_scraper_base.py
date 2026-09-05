"""Tests for is_end_of_pagination — the single decision that authorises
stale detection to mark listings as removed.

This function has now been wrong three times, in three different ways, and
each time MIN_SCRAPE_RATIO was the only thing that prevented a mass
false-delisting:

  2026-08-12  A capped crawl was treated as complete.       (ADR 0008)
  2026-08-26  A 503 streak was treated as complete.         (ADR 0008 amend)
  2026-09-02  A 403 streak was treated as complete.         (this file)

The third one is the reason these tests exist. The ADR's table said 404/410;
the code said `400 <= status < 500`. Nobody noticed the disagreement because
nothing exercised it — until BuyRentKenya started refusing GitHub's runners
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
    """No HTTP status at all — a timeout, a DNS failure, a reset. We learned
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
    reason in the ADR, not a quiet boundary change — the last time it was a
    range, it silently included every 4xx."""
    assert END_OF_LIST_STATUSES == frozenset({404, 410})

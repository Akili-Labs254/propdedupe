"""Shared scraper foundation.

Every source (Property24, BuyRentKenya, ...) parses different HTML/JSON but must
emit the SAME normalized shape: a `ScrapedListing`. That common output is what
lets the dedup engine treat all sources uniformly.

This is the 'per-source strategy, common output' pattern: each scraper
subclasses BaseScraper and implements parse(), but the rest of the pipeline
never needs to know which site a listing came from.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import requests

from backend.common import config


class ParserOutOfDate(RuntimeError):
    """The source's markup changed and parse() can no longer read it.

    Raised INSTEAD of returning an empty list, and the distinction is the
    whole point. An empty parse is indistinguishable from a genuinely empty
    page, so the scraper reads it as the end of pagination, reports
    `complete=True`, and clears stale detection to mark every unseen
    listing as removed from the site.

    That is exactly what BRK's 2026-09-01 re-platform triggered: the parser
    silently returned [] on page 1 and the run reported a complete crawl of
    zero listings. Only MIN_SCRAPE_RATIO stood between that and ~13,800
    listings being marked dead.

    An exception carries no HTTP status, so `is_end_of_pagination` returns
    False for it (ADR 0008): the run fails closed, `complete` stays False,
    and the stale pass is skipped. Loud and safe beats quiet and wrong.
    """


@dataclass
class ScrapedListing:
    """The normalized listing shape every scraper must produce."""

    source_listing_id: str
    url: str | None = None
    title: str | None = None
    price: float | None = None
    location: str | None = None
    bedrooms: int | None = None
    image_urls: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    baths: Optional[float] = None
    size_sqm: Optional[float] = None
    address: Optional[str] = None


class BaseScraper:
    """Base class for all source scrapers."""

    source_name: str = "base"
    base_url: str = ""

    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": config.USER_AGENT})

    def fetch(self, url: str) -> str:
        """Politely fetch a page and return its HTML.

        Rate-limited, with exponential backoff on 429/5xx (the vision
        doc's polite-indexer rule, finally implemented: P24 rate-limits
        bursts from datacenter IPs with 503s that pass on retry).
        Hard errors (404 etc.) raise immediately so end-of-pagination
        detection still works.
        """
        last_resp = None
        for extra_wait in (0, 10, 30, 60):
            if extra_wait:
                print(f"  {last_resp.status_code} — backing off {extra_wait}s...")
                time.sleep(extra_wait)
            time.sleep(config.REQUEST_DELAY_SECONDS)
            resp = self.session.get(url, timeout=30)
            if resp.status_code == 429 or resp.status_code >= 500:
                last_resp = resp
                continue
            resp.raise_for_status()
            return resp.text
        last_resp.raise_for_status()
        return last_resp.text  # unreachable; keeps type-checkers calm

    def parse(self, html: str) -> list[ScrapedListing]:
        """Each source implements this. Returns the listings from one page."""
        raise NotImplementedError


# The ONLY statuses that mean "there is no such page". Everything else
# fails closed. Kept as an explicit set rather than a range so that adding
# a status is a deliberate decision with a reason, not a boundary tweak.
END_OF_LIST_STATUSES = frozenset({404, 410})


def is_end_of_pagination(exc: Exception) -> bool:
    """Did this exception mean "past the last page", or "server unwell"?

    Both sites signal the end of a listing index by 404ing rather than
    serving an empty page, so a failure streak is the normal way a full
    crawl terminates. But a 503 streak looks identical to the caller,
    and on 2026-08-26 P24 threw 503s after 58 of 522 sale pages, the
    scraper reported complete=True, and stale detection was one guard
    away from marking 41,000 live listings dead.

    So: "not found" means we genuinely walked off the end of the list.
    Anything else (5xx, a timeout, a connection error, or a refusal)
    means we learned nothing about where the list ends, and the crawl must
    not be called complete.

    BaseScraper.fetch already retries 429/5xx with backoff (0/10/30/60s),
    so anything reaching here has survived ~100 seconds of trying.

    2026-09-02, NOT all of 4xx. This previously read
    `400 <= status < 500`, which disagreed with ADR 0008's own table
    (404/410 only) and swallowed every "you may not have this" status.
    BuyRentKenya began returning **403** to GitHub's runners that day;
    all three indexes reported `complete=True` on zero listings, and
    MIN_SCRAPE_RATIO was again the only thing standing between that and
    12,252 listings marked dead.

    The distinction that matters is refusal vs absence:

    | status              | complete | why                              |
    |---------------------|----------|----------------------------------|
    | 404, 410            | True     | the page is not there            |
    | 401, 403            | False    | it is there; we were refused     |
    | 429                 | False    | rate limited, not finished       |
    | other 4xx           | False    | fail closed on the unfamiliar    |
    | 5xx / timeout / conn| False    | server unwell, we learned nothing|
    """
    resp = getattr(exc, "response", None)
    status = getattr(resp, "status_code", None) if resp is not None else None
    return status in END_OF_LIST_STATUSES

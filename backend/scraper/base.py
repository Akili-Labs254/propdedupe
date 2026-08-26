"""Shared scraper foundation.

Every source (Property24, BuyRentKenya, ...) parses different HTML/JSON but must
emit the SAME normalized shape: a `ScrapedListing`. That common output is what
lets the dedup engine treat all sources uniformly.

This is the 'per-source strategy, common output' pattern — each scraper
subclasses BaseScraper and implements parse(), but the rest of the pipeline
never needs to know which site a listing came from.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import requests

from backend.common import config


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
        doc's polite-indexer rule, finally implemented — P24 rate-limits
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


def is_end_of_pagination(exc: Exception) -> bool:
    """Did this exception mean "past the last page", or "server unwell"?

    Both sites signal the end of a listing index by 404ing rather than
    serving an empty page, so a failure streak is the normal way a full
    crawl terminates. But a 503 streak looks identical to the caller —
    and on 2026-08-26 P24 threw 503s after 58 of 522 sale pages, the
    scraper reported complete=True, and stale detection was one guard
    away from marking 41,000 live listings dead.

    So: 4xx means we genuinely walked off the end of the list. 5xx, a
    timeout, or a connection error means we learned nothing about where
    the list ends, and the crawl must not be called complete.

    BaseScraper.fetch already retries 429/5xx with backoff (0/10/30/60s),
    so anything reaching here has survived ~100 seconds of trying.
    """
    resp = getattr(exc, "response", None)
    status = getattr(resp, "status_code", None) if resp is not None else None
    return status is not None and 400 <= status < 500

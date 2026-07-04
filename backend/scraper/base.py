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


class BaseScraper:
    """Base class for all source scrapers."""

    source_name: str = "base"
    base_url: str = ""

    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": config.USER_AGENT})

    def fetch(self, url: str) -> str:
        """Politely fetch a page and return its HTML. Rate-limited."""
        time.sleep(config.REQUEST_DELAY_SECONDS)
        resp = self.session.get(url, timeout=30)
        resp.raise_for_status()
        return resp.text

    def parse(self, html: str) -> list[ScrapedListing]:
        """Each source implements this. Returns the listings from one page."""
        raise NotImplementedError

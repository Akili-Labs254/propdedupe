"""Suburb normalisation must survive a source changing its location format.

2026-09-02: BuyRentKenya's re-platform started rendering location as
"Buruburu, Nairobi" where the old JSON-LD gave "Buruburu". The prefix
fallback in normalize_suburb only matched on `suburb + " "`, so the comma
stopped it firing and every area silently split in two:

    kilimani             13 properties   (Property24)
    kilimani, nairobi   424 properties   (BuyRentKenya)

Each half got its own segment page, neither was complete, and the Kilimani
1-bed page the Google Ads test was about to point at showed 13 results
instead of 437. Nothing errored; the damage was entirely in the shape of
the data.
"""

import pytest

from backend.dedup.adjacency import normalize_suburb


@pytest.mark.parametrize("raw,expected", [
    ("Kilimani, Nairobi", "kilimani"),
    ("Kileleshwa, Nairobi", "kileleshwa"),
    ("Lavington, Nairobi", "lavington"),
    ("Westlands, Nairobi", "westlands"),
    ("Runda, Nairobi, Kenya", "runda"),
    ("Nyali, Mombasa", "nyali"),
])
def test_city_qualifier_is_stripped(raw, expected):
    assert normalize_suburb(raw) == expected


def test_bare_suburb_unchanged():
    assert normalize_suburb("Kilimani") == "kilimani"
    assert normalize_suburb("Westlands") == "westlands"


def test_both_source_formats_agree():
    """The actual requirement: P24's format and BRK's format must land on
    the same key, or the corpus splits down the middle."""
    assert normalize_suburb("Kilimani") == normalize_suburb("Kilimani, Nairobi")
    assert normalize_suburb("Runda") == normalize_suburb("Runda, Nairobi, Kenya")


def test_city_name_that_is_itself_a_suburb_survives():
    """"Nairobi CBD" must not be stripped to nothing — qualifiers come off
    the end only, and never take the whole name."""
    assert normalize_suburb("Nairobi CBD") == "nairobi cbd"
    assert normalize_suburb("Nairobi Cbd, Nairobi") == "nairobi cbd"


def test_existing_behaviour_preserved():
    """The prefix/alias machinery this sits in front of must still work."""
    assert normalize_suburb("Off Kiambu Road") == "kiambu road"
    assert normalize_suburb("Westlands Area") == "westlands"
    assert normalize_suburb("Lavington Green.") == "lavington"
    assert normalize_suburb("") == ""

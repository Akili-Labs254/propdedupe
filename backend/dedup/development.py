"""The development key: grouping adverts that are identical on every
human-readable field.

Cross-source dedup (step 1) and the intra-P24 `cluster_key` (step 2) are
both image-keyed, so neither catches a development advertised unit by unit:
those adverts carry *different photographs of the same building*, and the
hashes correctly report them as different images.

This key ignores images entirely and asks the opposite question: is every
word a buyer can read the same? See ADR 0009.

MIRRORS cluster_key's discipline: if this formula is ever reimplemented in
SQL, the two must produce byte-for-byte identical output. Hence the
explicit canonicalisation below rather than f-string interpolation, which
would render 7000000 and 7000000.0 differently depending on the column
type the driver happened to hand back.
"""

from typing import Optional

# Stands in for NULL so that "bathrooms unknown" groups with "bathrooms
# unknown" instead of each row becoming its own singleton. Chosen because
# it cannot occur in any real value.
NULL_SENTINEL = "~"

# Below this, a group is not treated as a development. Measured on the
# 2026-08-28 corpus: groups of 2 and 3 account for only 23% of the possible
# collapse but carry nearly all of the false-merge risk, because two adverts
# identical on every field are genuinely ambiguous: they may be two
# different flats, or one flat photographed by two agents. Groups of 4+ are
# not plausibly independent coincidences across eight fields. ADR 0009.
MIN_DEVELOPMENT_GROUP = 4


def _text(value: Optional[str]) -> str:
    """Canonicalise a text field: strip, casefold, collapse inner runs of
    whitespace. Portal titles vary by trailing spaces and capitalisation
    far more often than by meaning."""
    if value is None:
        return NULL_SENTINEL
    cleaned = " ".join(str(value).split()).casefold()
    return cleaned or NULL_SENTINEL


def _num(value) -> str:
    """Canonicalise a numeric field to a stable string.

    float(7000000) and Decimal("7000000.00") must render the same, or the
    key silently splits a group by which column type the row came from.
    """
    if value is None:
        return NULL_SENTINEL
    try:
        f = float(value)
    except (TypeError, ValueError):
        return NULL_SENTINEL
    # Integral values lose the ".0"; fractional ones keep 2dp. Sizes and
    # bath counts are the only fields here that are ever fractional.
    if f == int(f):
        return str(int(f))
    return "{:.2f}".format(f)


def development_key(row: dict) -> Optional[str]:
    """Return the development key for a listing or property row.

    Returns None when the row lacks the fields that make the key meaningful.
    A row without a price, suburb or bed count cannot be confidently grouped
    with anything, and must stay a singleton: this is the same fail-closed
    stance ADR 0008 takes on incomplete crawls.

    Accepts both listing rows (`baths`) and property rows (`bathrooms`).
    """
    price = row.get("price")
    suburb = row.get("suburb")
    bedrooms = row.get("bedrooms")
    title = row.get("title")
    location = row.get("location")

    if price is None or suburb is None or bedrooms is None:
        return None
    if not title or not location:
        return None

    # listings call it `baths`, properties call it `bathrooms`. Reading the
    # wrong one yields NULL for every row and silently merges on a weaker
    # key, so take whichever is present rather than defaulting to None.
    baths = row.get("bathrooms")
    if baths is None:
        baths = row.get("baths")

    return "|".join([
        _text(row.get("category")),
        _text(suburb),
        _text(location),
        _text(title),
        _num(bedrooms),
        _num(baths),
        _num(row.get("size_sqm")),
        _num(price),
    ])

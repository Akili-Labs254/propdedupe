"""The rebuild's input gate.

persist_dedup clears the live tables first and rebuilds after, so its
inputs must be checked before it starts. On 24 Nov 2026 an image-parser
break left 26,008 active listings with no image; a rebuild would have
merged 21,909 of them into 2,992 false properties. Nothing checked.
"""
import pytest

import scripts.persist_dedup as pd


def health(active=60000, no_image=0, unhashed=0, no_price=600):
    return dict(active=active, no_image=no_image, unhashed=unhashed,
                no_price=no_price)


def test_normal_inputs_pass():
    assert pd.preflight_problems(health()) == []


def test_the_24_nov_state_is_refused():
    problems = pd.preflight_problems(
        health(active=66000, no_image=26008, unhashed=0))
    assert len(problems) == 1 and "no image" in problems[0]


def test_unhashed_inputs_are_refused():
    """Rebuilding before the hash stage finishes drops thousands of
    listings out of image matching for a whole build."""
    problems = pd.preflight_problems(health(unhashed=20000))
    assert any("run refresh --hashes" in p for p in problems)


def test_price_collapse_is_refused():
    """The August shape: 37,000 prices went NULL."""
    assert pd.preflight_problems(health(no_price=37000))


def test_a_few_gaps_are_fine():
    assert pd.preflight_problems(
        health(no_image=500, unhashed=1000, no_price=3000)) == []


def test_no_active_listings_is_refused():
    assert pd.preflight_problems(health(active=0))


def test_main_aborts_before_clearing(monkeypatch):
    cleared = []
    monkeypatch.setattr(pd, "get_client", lambda **k: object())
    monkeypatch.setattr(pd, "input_health",
                        lambda db: health(active=66000, no_image=26008))
    monkeypatch.setattr(pd, "clear_previous_results",
                        lambda db: cleared.append(True))
    monkeypatch.setattr(pd, "snapshot_slug_state",
                        lambda db: cleared.append("snapshot") or {})
    with pytest.raises(SystemExit) as info:
        pd.main()
    assert "ABORTED" in str(info.value)
    assert cleared == []

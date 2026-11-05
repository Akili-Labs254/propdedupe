"""Slug-snapshot resolution tests.

The rebuild snapshots {listing_id: slug} before clearing, so rebuilt
properties inherit stable public URLs. A rebuild that dies partway leaves a
partial properties table; re-running then snapshots THAT. The old code only
fell back to the crash-safe copy when the live snapshot was completely
empty, so a partial state (6,412 of ~28,000 properties) sailed through and
overwrote the good copy: churning 26,346 URLs on 2026-08-03.

Run: pytest tests/test_persist.py
"""
from scripts.persist_dedup import (
    SNAPSHOT_SHRINK_GUARD,
    _group_by_cluster_key,
    resolve_slug_snapshot,
)


def stored(n):
    """A stored snapshot of n listings, slugs distinct from live ones."""
    return lambda: {i: f"stored-slug-{i}" for i in range(n)}


# ── the incident ─────────────────────────────────────────────────────

def test_partial_state_does_not_overwrite_stored():
    """The 2026-08-03 regression: 27,968 live against 54,536 stored."""
    live = {i: f"live-{i}" for i in range(27_968)}
    snap, persist, note = resolve_slug_snapshot(live, 54_536, stored(54_536))
    assert persist is False, "must not overwrite a good snapshot"
    assert "WARNING" in note
    assert len(snap) == 54_536          # stored base, live overlaid


def test_partial_state_lets_live_win_on_conflict():
    live = {1: "live-1"}
    snap, _, _ = resolve_slug_snapshot(live, 100, stored(100))
    assert snap[1] == "live-1"          # live is more current
    assert snap[2] == "stored-slug-2"   # stored fills the gap


# ── the dangerous false positive ─────────────────────────────────────

def test_normal_run_still_persists():
    """If a normal run refused to persist, the snapshot would go stale
    forever and new listings would never record their slugs."""
    live = {i: f"live-{i}" for i in range(28_000)}
    snap, persist, _ = resolve_slug_snapshot(live, 27_500, stored(27_500))
    assert persist is True
    assert snap == live


def test_modest_shrinkage_still_persists():
    """A stale cleanup shrinks the corpus legitimately. Observed run-to-run
    movement is single-digit percent (32,757 -> 31,825 = 3%), so a 20% drop
    must still be treated as normal."""
    live = {i: f"live-{i}" for i in range(8_000)}
    snap, persist, _ = resolve_slug_snapshot(live, 10_000, stored(10_000))
    assert persist is True
    assert snap == live


def test_guard_boundary_is_exactly_at_threshold():
    """Exactly at the threshold is treated as normal, not a crash."""
    live = {i: str(i) for i in range(70)}
    _, persist, _ = resolve_slug_snapshot(live, 100, stored(100))
    assert persist is True
    assert SNAPSHOT_SHRINK_GUARD == 0.7


# ── first-run and degenerate paths ───────────────────────────────────

def test_first_run_ever():
    snap, persist, note = resolve_slug_snapshot({}, 0, stored(0))
    assert snap == {} and persist is False
    assert "first run" in note


def test_first_run_with_data_persists():
    live = {1: "a", 2: "b"}
    snap, persist, _ = resolve_slug_snapshot(live, 0, stored(0))
    assert persist is True and snap == live


def test_empty_live_recovers_stored():
    """Total wipe: the pre-existing recovery path must still work."""
    snap, persist, note = resolve_slug_snapshot({}, 500, stored(500))
    assert len(snap) == 500
    assert persist is False
    assert "RECOVERED" in note


def test_stored_table_unreadable_is_survivable():
    """load_stored returning {} (missing table) must not crash or wipe."""
    live = {1: "a"}
    snap, persist, _ = resolve_slug_snapshot(live, 9_999, lambda: {})
    assert snap == live          # nothing to recover, fall through to live
    assert persist is True


# ── cluster_key grouping (identical-image expansion) ─────────────────

def test_group_by_cluster_key_splits_variants():
    details = {1: {"cluster_key": "A"}, 2: {"cluster_key": "A"},
               3: {"cluster_key": "B"}}
    assert sorted(map(len, _group_by_cluster_key([1, 2, 3], details))) == [1, 2]


def test_group_by_cluster_key_isolates_falsy_keys():
    """Falsy cluster_keys must each become their own group: lumping them
    produced listing_a_id == listing_b_id and tripped matches_pair_order."""
    details = {1: {"cluster_key": None}, 2: {"cluster_key": ""}}
    assert len(_group_by_cluster_key([1, 2], details)) == 2


def test_group_by_cluster_key_drops_unknown_ids():
    assert _group_by_cluster_key([1, 999], {1: {"cluster_key": "A"}}) == [[1]]


# ── crashed rebuild left listings unlinked (5 Nov 2026) ──────────────

def test_unlinked_listings_keep_their_stored_slug():
    live = {i: f"live-{i}" for i in range(9_000)}
    snap, persist, note = resolve_slug_snapshot(
        live, 10_000, stored(10_000), unlinked_ids={9_500, 9_501, 20_000})
    assert persist is True
    assert snap[9_500] == "stored-slug-9500"   # recovered
    assert 20_000 not in snap                  # unknown to stored: nothing to recover
    assert snap[5] == "live-5"                 # live untouched
    assert len(snap) == 9_002 and "recovered" in note


def test_departed_listings_still_drop_out():
    """Recovery is only for unlinked listings: stored ids that simply left
    the corpus must not be kept forever."""
    live = {i: f"live-{i}" for i in range(8_000)}
    snap, _, _ = resolve_slug_snapshot(live, 10_000, stored(10_000),
                                       unlinked_ids=set())
    assert snap == live

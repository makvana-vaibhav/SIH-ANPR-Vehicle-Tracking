"""Watchlist matching: exact hits, near misses, and the things that must not fire.

The failure that matters most here is a BOLO that silently never matches. These
tests pin the cases where that could happen quietly.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.models.enums import AlertType, Priority
from app.services.watchlist import (
    WatchlistEntry,
    WatchlistIndex,
    _deletion_variants,
    _within_one_edit,
)


def entry(plate: str, priority: str = Priority.HIGH.value, **kw) -> WatchlistEntry:
    return WatchlistEntry(
        id=kw.get("id", uuid.uuid4()),
        plate=plate,
        category=kw.get("category", "stolen"),
        priority=priority,
        case_ref=kw.get("case_ref"),
        valid_from=kw.get("valid_from"),
        valid_to=kw.get("valid_to"),
    )


def index_of(*entries: WatchlistEntry) -> WatchlistIndex:
    """An index built directly, so tests need no database."""
    idx = WatchlistIndex()
    for e in entries:
        idx.exact.setdefault(e.plate, []).append(e)
        for variant in _deletion_variants(e.plate):
            idx.variants.setdefault(variant, []).append(e)
    idx.entry_count = len(entries)
    idx._dirty = False
    idx.loaded_at = 1e12  # far future, so needs_refresh stays False
    return idx


class TestEditDistance:
    @pytest.mark.parametrize(
        "a,b,expected",
        [
            ("GJ03AB1234", "GJ03AB1234", True),  # identical
            ("GJ03AB1234", "GJ03AB1235", True),  # one substitution
            ("GJ03AB1234", "GJ03AB123", True),  # one deletion
            ("GJ03AB1234", "GJ03AB12345", True),  # one insertion
            ("GJ03AB1234", "GJ03AB1256", False),  # two substitutions
            ("GJ03AB1234", "MH12XY9876", False),  # unrelated
            ("GJ03AB1234", "GJ03AB12", False),  # two deletions
        ],
    )
    def test_within_one_edit(self, a: str, b: str, expected: bool) -> None:
        assert _within_one_edit(a, b) is expected
        assert _within_one_edit(b, a) is expected, "must be symmetric"


class TestMatching:
    @pytest.mark.asyncio
    async def test_exact_hit_keeps_the_entry_priority(self) -> None:
        idx = index_of(entry("GJ03AB1234", Priority.CRITICAL.value))
        match = await idx.match("GJ03AB1234")
        assert match is not None
        assert match.exact is True
        assert match.alert_type == AlertType.WATCHLIST_HIT.value
        assert match.priority == Priority.CRITICAL.value

    @pytest.mark.asyncio
    async def test_near_match_is_capped_below_the_entry_priority(self) -> None:
        """A one-character difference is a lead, not a confirmed hit.

        Raising it as critical would train operators to distrust critical.
        """
        idx = index_of(entry("GJ03AB1234", Priority.CRITICAL.value))
        match = await idx.match("GJ03AB1284")
        assert match is not None
        assert match.exact is False
        assert match.alert_type == AlertType.POSSIBLE_MATCH.value
        assert match.priority == Priority.MEDIUM.value

    @pytest.mark.asyncio
    async def test_unrelated_plate_does_not_match(self) -> None:
        idx = index_of(entry("GJ03AB1234"))
        assert await idx.match("MH12XY9876") is None

    @pytest.mark.asyncio
    async def test_two_characters_off_does_not_match(self) -> None:
        """The line has to be somewhere, and two edits is too loose.

        At two edits a busy junction starts generating leads for plates that
        merely rhyme with a BOLO.
        """
        idx = index_of(entry("GJ03AB1234"))
        assert await idx.match("GJ03AB1256") is None

    @pytest.mark.asyncio
    async def test_exact_wins_over_near(self) -> None:
        idx = index_of(
            entry("GJ03AB1234", Priority.LOW.value),
            entry("GJ03AB1235", Priority.CRITICAL.value),
        )
        match = await idx.match("GJ03AB1234")
        assert match is not None and match.exact is True
        assert match.entry.plate == "GJ03AB1234"

    @pytest.mark.asyncio
    async def test_highest_priority_entry_wins_among_equals(self) -> None:
        shared = "GJ03AB1234"
        idx = index_of(
            entry(shared, Priority.LOW.value),
            entry(shared, Priority.CRITICAL.value),
        )
        match = await idx.match(shared)
        assert match is not None
        assert match.priority == Priority.CRITICAL.value

    @pytest.mark.asyncio
    async def test_matching_is_case_insensitive(self) -> None:
        idx = index_of(entry("GJ03AB1234"))
        assert await idx.match("gj03ab1234") is not None

    @pytest.mark.asyncio
    async def test_empty_plate_never_matches(self) -> None:
        idx = index_of(entry("GJ03AB1234"))
        assert await idx.match("") is None


class TestValidityWindow:
    @pytest.mark.asyncio
    async def test_an_expired_bolo_does_not_fire(self) -> None:
        """An entry that never expires is a surveillance liability."""
        past = datetime.now(UTC) - timedelta(days=1)
        idx = index_of(entry("GJ03AB1234", valid_to=past))
        assert await idx.match("GJ03AB1234") is None

    @pytest.mark.asyncio
    async def test_a_future_bolo_does_not_fire_yet(self) -> None:
        future = datetime.now(UTC) + timedelta(days=1)
        idx = index_of(entry("GJ03AB1234", valid_from=future))
        assert await idx.match("GJ03AB1234") is None

    @pytest.mark.asyncio
    async def test_an_entry_inside_its_window_fires(self) -> None:
        now = datetime.now(UTC)
        idx = index_of(
            entry(
                "GJ03AB1234", valid_from=now - timedelta(days=1), valid_to=now + timedelta(days=1)
            )
        )
        assert await idx.match("GJ03AB1234") is not None

    @pytest.mark.asyncio
    async def test_validity_is_checked_at_the_detection_time(self) -> None:
        """A detection is judged against the window as it was then."""
        now = datetime.now(UTC)
        idx = index_of(entry("GJ03AB1234", valid_to=now - timedelta(hours=1)))
        assert await idx.match("GJ03AB1234", when=now - timedelta(hours=2)) is not None
        assert await idx.match("GJ03AB1234", when=now) is None

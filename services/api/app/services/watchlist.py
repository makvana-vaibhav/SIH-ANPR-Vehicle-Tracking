"""Watchlist matching.

Runs on every settled detection, so it has to be fast enough to sit in the
event path and precise enough that an operator trusts it. Two kinds of match,
and keeping them apart is the whole point:

* **Exact** on the normalised plate — a confirmed hit, raised at the watchlist
  entry's own priority.
* **Near** — within one character. OCR misreads a single character often enough
  that requiring exactness would lose real hits, but a near match is a lead and
  not a fact. It is raised as ``possible_match`` at a deliberately lower
  priority, so a control room can act on confirmed hits first and still see the
  maybes.

The index is held in memory and rebuilt when the watchlist changes. A watchlist
lookup per detection against Postgres would put a query on the hot path for
data that changes a few times a day.

Near-matching uses a deletion index rather than comparing against every entry:
two strings within one edit share a variant formed by deleting one character
from each. That makes a lookup proportional to plate length instead of to the
size of the watchlist, which is what lets it stay on the event path as the
watchlist grows.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.models.enums import AlertType, Priority
from app.models.intelligence import Watchlist

log = get_logger(__name__)

#: Rebuild at least this often even if nothing signalled a change, so an entry
#: edited directly in the database cannot stay invisible indefinitely.
REFRESH_SECONDS = 60.0

#: A near match is never raised above this, whatever the entry's own priority.
#: A one-character-different plate is a lead; treating it as critical would
#: train operators to distrust critical.
NEAR_MATCH_MAX_PRIORITY = Priority.MEDIUM


@dataclass(frozen=True, slots=True)
class WatchlistEntry:
    id: Any
    plate: str
    category: str
    priority: str
    case_ref: str | None
    valid_from: datetime | None
    valid_to: datetime | None

    def is_valid_at(self, when: datetime) -> bool:
        """A BOLO outside its window is not a hit.

        Validity is enforced at match time rather than at load time so an entry
        that expires between refreshes stops matching immediately.
        """
        if self.valid_from is not None and when < self.valid_from:
            return False
        return not (self.valid_to is not None and when > self.valid_to)


@dataclass(frozen=True, slots=True)
class Match:
    entry: WatchlistEntry
    exact: bool
    distance: int

    @property
    def alert_type(self) -> str:
        return AlertType.WATCHLIST_HIT.value if self.exact else AlertType.POSSIBLE_MATCH.value

    @property
    def priority(self) -> str:
        if self.exact:
            return self.entry.priority
        return _capped_priority(self.entry.priority)


_PRIORITY_ORDER = [
    Priority.LOW.value,
    Priority.MEDIUM.value,
    Priority.HIGH.value,
    Priority.CRITICAL.value,
]


def _capped_priority(priority: str) -> str:
    try:
        index = _PRIORITY_ORDER.index(priority)
    except ValueError:
        return NEAR_MATCH_MAX_PRIORITY.value
    ceiling = _PRIORITY_ORDER.index(NEAR_MATCH_MAX_PRIORITY.value)
    return _PRIORITY_ORDER[min(index, ceiling)]


def _deletion_variants(plate: str) -> set[str]:
    """Every string formed by deleting one character, plus the original.

    Two strings within edit distance 1 always share at least one of these.
    """
    return {plate} | {plate[:i] + plate[i + 1 :] for i in range(len(plate))}


@dataclass
class WatchlistIndex:
    """In-memory watchlist, refreshed on change."""

    exact: dict[str, list[WatchlistEntry]] = field(default_factory=dict)
    variants: dict[str, list[WatchlistEntry]] = field(default_factory=dict)
    loaded_at: float = 0.0
    entry_count: int = 0
    _dirty: bool = True

    def mark_stale(self) -> None:
        """Called when the watchlist is mutated, so the next match reloads."""
        self._dirty = True

    @property
    def needs_refresh(self) -> bool:
        return self._dirty or (time.monotonic() - self.loaded_at) > REFRESH_SECONDS

    async def refresh(self) -> None:
        async with SessionLocal() as session:
            rows = (
                (await session.execute(select(Watchlist).where(Watchlist.active.is_(True))))
                .scalars()
                .all()
            )

        exact: dict[str, list[WatchlistEntry]] = {}
        variants: dict[str, list[WatchlistEntry]] = {}
        for row in rows:
            entry = WatchlistEntry(
                id=row.id,
                plate=row.plate_normalised.upper(),
                category=row.category,
                priority=row.priority,
                case_ref=row.case_ref,
                valid_from=row.valid_from,
                valid_to=row.valid_to,
            )
            exact.setdefault(entry.plate, []).append(entry)
            for variant in _deletion_variants(entry.plate):
                variants.setdefault(variant, []).append(entry)

        self.exact = exact
        self.variants = variants
        self.entry_count = len(rows)
        self.loaded_at = time.monotonic()
        self._dirty = False
        log.info("watchlist.indexed", entries=self.entry_count)

    async def match(self, plate: str, when: datetime | None = None) -> Match | None:
        """Best match for a plate, or None.

        Exact wins over near, always. Among equals, the highest priority entry
        wins, so a stolen-vehicle BOLO is not masked by a routine one.
        """
        if not plate:
            return None
        if self.needs_refresh:
            await self.refresh()

        moment = when or datetime.now(UTC)
        plate = plate.upper()

        exact = [e for e in self.exact.get(plate, []) if e.is_valid_at(moment)]
        if exact:
            return Match(entry=_highest(exact), exact=True, distance=0)

        candidates: dict[Any, WatchlistEntry] = {}
        for variant in _deletion_variants(plate):
            for entry in self.variants.get(variant, []):
                candidates[entry.id] = entry

        near = [
            entry
            for entry in candidates.values()
            if entry.is_valid_at(moment) and _within_one_edit(plate, entry.plate)
        ]
        if near:
            return Match(entry=_highest(near), exact=False, distance=1)
        return None


def _highest(entries: list[WatchlistEntry]) -> WatchlistEntry:
    return max(
        entries,
        key=lambda e: _PRIORITY_ORDER.index(e.priority) if e.priority in _PRIORITY_ORDER else -1,
    )


def _within_one_edit(a: str, b: str) -> bool:
    """True when a and b differ by at most one insert, delete or substitution.

    Written out rather than calling a general edit-distance routine: this runs
    on every detection, and the bounded case is a few comparisons instead of a
    full dynamic-programming table.
    """
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False

    if len(a) == len(b):
        differences = sum(1 for x, y in zip(a, b, strict=True) if x != y)
        return differences <= 1

    shorter, longer = (a, b) if len(a) < len(b) else (b, a)
    i = j = 0
    skipped = False
    while i < len(shorter) and j < len(longer):
        if shorter[i] != longer[j]:
            if skipped:
                return False
            skipped = True
            j += 1
            continue
        i += 1
        j += 1
    return True


#: Process-wide index. The consumer and the API share it, so a watchlist edit
#: through the API is visible to matching without a round trip.
index = WatchlistIndex()

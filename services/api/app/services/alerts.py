"""Raising alerts, and moving them through their lifecycle.

Two things here are load-bearing for whether a control room trusts this system.

**Deduplication.** A vehicle stopped at a junction is detected repeatedly, and a
watchlisted one would otherwise raise an alert every time. Forty alerts for one
car is worse than none: operators stop reading them. So a hit on the same plate
at the same camera is suppressed for a window, and the suppression is counted on
the original alert rather than discarded — "seen 12 times" is useful, twelve
rows are not.

The window is per (plate, camera). The same plate at a *different* camera is a
new alert on purpose: that is the vehicle moving, which is exactly what a
cross-camera system exists to notice.

**Lifecycle.** new -> acknowledged -> dispatched -> closed / false_positive,
and every transition records who and when. `false_positive` is a first-class
outcome, not a deletion: a system that lets operators quietly erase its mistakes
cannot be audited, and the false positives are the data that improves it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.enums import AlertStatus
from app.models.intelligence import Alert
from app.services.watchlist import Match

log = get_logger(__name__)

#: How long a repeat hit on the same plate at the same camera is folded into
#: the existing alert instead of raising a new one.
DEDUP_WINDOW = timedelta(seconds=90)

#: Which transitions are legal. An alert cannot go from closed back to new, and
#: attempting it is an error rather than something that silently succeeds.
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    AlertStatus.NEW.value: {
        AlertStatus.ACKNOWLEDGED.value,
        AlertStatus.FALSE_POSITIVE.value,
        AlertStatus.CLOSED.value,
    },
    AlertStatus.ACKNOWLEDGED.value: {
        AlertStatus.DISPATCHED.value,
        AlertStatus.CLOSED.value,
        AlertStatus.FALSE_POSITIVE.value,
    },
    AlertStatus.DISPATCHED.value: {
        AlertStatus.CLOSED.value,
        AlertStatus.FALSE_POSITIVE.value,
    },
    AlertStatus.CLOSED.value: set(),
    AlertStatus.FALSE_POSITIVE.value: set(),
}


class InvalidTransition(ValueError):
    """Raised when a lifecycle move is not permitted from the current status."""


@dataclass
class _RecentHit:
    alert_id: uuid.UUID
    raised_at: datetime
    repeats: int = 0


@dataclass
class AlertDeduper:
    """Remembers recent alerts so repeats fold into them.

    Kept in memory rather than queried: this runs on the event path, and the
    window is short enough that a restart losing it costs at most one duplicate
    alert per vehicle in flight.
    """

    window: timedelta = DEDUP_WINDOW
    _recent: dict[tuple[str, str], _RecentHit] = field(default_factory=dict)

    def _prune(self, now: datetime) -> None:
        cutoff = now - self.window
        stale = [key for key, hit in self._recent.items() if hit.raised_at < cutoff]
        for key in stale:
            del self._recent[key]

    def seen(self, plate: str, camera: str, now: datetime | None = None) -> _RecentHit | None:
        moment = now or datetime.now(UTC)
        self._prune(moment)
        hit = self._recent.get((plate, camera))
        if hit is None:
            return None
        hit.repeats += 1
        return hit

    def remember(
        self, plate: str, camera: str, alert_id: uuid.UUID, now: datetime | None = None
    ) -> None:
        self._recent[(plate, camera)] = _RecentHit(
            alert_id=alert_id, raised_at=now or datetime.now(UTC)
        )

    def clear(self) -> None:
        self._recent.clear()


deduper = AlertDeduper()


async def raise_for_match(
    session: AsyncSession,
    *,
    match: Match,
    plate: str,
    camera_id: uuid.UUID | None,
    camera_code: str,
    detection_id: uuid.UUID | None,
    detection_ts: datetime | None,
    confidence: float | None,
) -> Alert | None:
    """Create an alert for a watchlist match, unless it is a recent repeat.

    Returns the new alert, or None when it was folded into a recent one.
    """
    now = datetime.now(UTC)
    repeat = deduper.seen(plate, camera_code, now)
    if repeat is not None:
        log.info(
            "alert.deduplicated",
            plate=plate,
            camera=camera_code,
            alert_id=str(repeat.alert_id),
            repeats=repeat.repeats,
        )
        return None

    alert = Alert(
        detection_id=detection_id,
        detection_ts=detection_ts,
        watchlist_id=match.entry.id,
        camera_id=camera_id,
        alert_type=match.alert_type,
        priority=match.priority,
        plate_normalised=plate,
        confidence=confidence,
        status=AlertStatus.NEW.value,
        notes=(
            None
            if match.exact
            else f"Near match to watchlist plate {match.entry.plate} "
            f"({match.distance} character different) — verify before acting"
        ),
    )
    session.add(alert)
    await session.flush()

    deduper.remember(plate, camera_code, alert.id, now)
    log.info(
        "alert.raised",
        alert_id=str(alert.id),
        plate=plate,
        camera=camera_code,
        alert_type=alert.alert_type,
        priority=alert.priority,
        exact=match.exact,
    )
    return alert


async def transition(
    session: AsyncSession,
    alert: Alert,
    *,
    to: str,
    user_id: uuid.UUID | None,
    notes: str | None = None,
) -> Alert:
    """Move an alert to a new status, recording who and when."""
    allowed = ALLOWED_TRANSITIONS.get(alert.status, set())
    if to not in allowed:
        raise InvalidTransition(
            f"cannot move an alert from '{alert.status}' to '{to}'; "
            f"allowed: {sorted(allowed) or 'none — this alert is final'}"
        )

    alert.status = to
    alert.acknowledged_by = user_id
    alert.acknowledged_at = datetime.now(UTC)
    if notes:
        alert.notes = f"{alert.notes}\n{notes}".strip() if alert.notes else notes

    await session.flush()
    log.info(
        "alert.transitioned",
        alert_id=str(alert.id),
        to=to,
        user=str(user_id) if user_id else None,
    )
    return alert


async def get_alert(session: AsyncSession, alert_id: uuid.UUID) -> Alert | None:
    return (await session.execute(select(Alert).where(Alert.id == alert_id))).scalar_one_or_none()


def alert_payload(alert: Alert, match: Match | None = None) -> dict[str, Any]:
    """Shape for the live feed. Carries enough to act on without a round trip."""
    payload: dict[str, Any] = {
        "event": "alert.raised",
        "alert_id": str(alert.id),
        "alert_type": alert.alert_type,
        "priority": alert.priority,
        "plate": alert.plate_normalised,
        "confidence": alert.confidence,
        "status": alert.status,
        "camera_id": str(alert.camera_id) if alert.camera_id else None,
        "detection_id": str(alert.detection_id) if alert.detection_id else None,
        "created_at": (alert.created_at or datetime.now(UTC)).isoformat(),
        "notes": alert.notes,
    }
    if match is not None:
        payload["watchlist"] = {
            "id": str(match.entry.id),
            "plate": match.entry.plate,
            "category": match.entry.category,
            "case_ref": match.entry.case_ref,
            "exact": match.exact,
        }
    return payload

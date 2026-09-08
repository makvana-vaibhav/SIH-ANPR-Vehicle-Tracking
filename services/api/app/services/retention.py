"""Enforcing the retention policy.

## Why this exists

`Settings` has carried `retention_detections_days`, `retention_media_days` and
`retention_audit_days` since Phase 0, and until now **nothing deleted
anything**. A surveillance platform that states a retention period and then
keeps everything forever is not merely untidy: under the DPDP Act the stated
period is the lawful basis for holding the data at all, and exceeding it is the
violation. A policy nobody enforces is worse than no policy, because it is
documented as a control that does not exist.

## How it deletes

Detections and camera health are TimescaleDB hypertables, so expiry is
`drop_chunks` — it unlinks whole time partitions rather than scanning and
deleting rows. On a table holding hundreds of millions of detections a
row-by-row `DELETE` would run for hours, hold locks, and bloat the table it was
trying to shrink.

The audit log is an ordinary table and is deleted by predicate. It is also the
smallest and the longest-lived, so that is affordable.

## What deliberately survives

**Alerts outlive the detections that raised them.** An alert is a police record
with a lifecycle, an operator's name against each transition, and possibly a
case reference; the raw sighting behind it is evidence with a shorter lawful
life. `Alert.detection_id` therefore has no foreign key and dangles by design
once the detection expires. `evidence_expired()` exists so a screen can say
"the evidence for this alert is past its retention date" rather than showing an
operator a lookup failure that looks like a bug.

**The audit log outlives everything else** — five years by default. It is the
record of who looked at whom, which is exactly what an inquiry into misuse
would need, and it must not be purgeable on the same schedule as the data it
describes.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.models.intelligence import Alert, Detection
from app.models.security import AuditLog

log = get_logger(__name__)

#: How often the sweep runs. Daily: retention is measured in months, so a
#: shorter interval buys nothing and a longer one lets an expiry sit for weeks.
SWEEP_INTERVAL_S = 24 * 3600.0

#: Hypertables, and which setting governs each.
HYPERTABLES = {
    "detections": "retention_detections_days",
    "camera_health": "retention_camera_health_days",
}


@dataclass(slots=True)
class SweepResult:
    """What one pass removed. Reported whether or not anything was deleted."""

    ran_at: datetime
    dry_run: bool
    chunks_dropped: dict[str, int] = field(default_factory=dict)
    audit_rows_deleted: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ran_at": self.ran_at.isoformat(),
            "dry_run": self.dry_run,
            "chunks_dropped": dict(self.chunks_dropped),
            "audit_rows_deleted": self.audit_rows_deleted,
            "errors": list(self.errors),
        }


def cutoff_for(days: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


async def _expire_hypertable(session: AsyncSession, table: str, days: int, dry_run: bool) -> int:
    """Drop whole time partitions older than the cutoff.

    `show_chunks` is asked first so the count is known and reportable even on a
    dry run — "retention ran and removed nothing" and "retention did not run"
    must not look the same in a log.
    """
    cutoff = cutoff_for(days)
    # Both parameters are cast explicitly. asyncpg infers parameter types from
    # the function signature, and `show_chunks` is polymorphic in `older_than`
    # (it accepts an interval, a timestamp, or an integer), so an uncast
    # parameter is genuinely ambiguous and the driver refuses it.
    chunks = (
        (
            await session.execute(
                text(
                    "SELECT show_chunks(CAST(:table AS regclass), "
                    "older_than => CAST(:cutoff AS timestamptz))"
                ),
                {"table": table, "cutoff": cutoff},
            )
        )
        .scalars()
        .all()
    )

    if not dry_run and chunks:
        await session.execute(
            text(
                "SELECT drop_chunks(CAST(:table AS regclass), "
                "older_than => CAST(:cutoff AS timestamptz))"
            ),
            {"table": table, "cutoff": cutoff},
        )
    return len(chunks)


async def _expire_audit(session: AsyncSession, days: int, dry_run: bool) -> int:
    cutoff = cutoff_for(days)
    doomed = (
        await session.execute(
            select(func.count()).select_from(AuditLog).where(AuditLog.ts < cutoff)
        )
    ).scalar_one()
    if not dry_run and doomed:
        await session.execute(delete(AuditLog).where(AuditLog.ts < cutoff))
    return doomed


async def sweep(dry_run: bool = False) -> SweepResult:
    """Delete everything past its retention date."""
    result = SweepResult(ran_at=datetime.now(UTC), dry_run=dry_run)

    # A session per target. Postgres aborts the whole transaction on a failed
    # statement, so sharing one would mean the first failure silently took the
    # rest of the sweep with it — a policy that reports success while enforcing
    # nothing is the worst of the available outcomes.
    for table, setting in HYPERTABLES.items():
        days = getattr(settings, setting, None)
        if not days:
            continue
        try:
            async with SessionLocal() as session:
                result.chunks_dropped[table] = await _expire_hypertable(
                    session, table, days, dry_run
                )
                if not dry_run:
                    await session.commit()
        except Exception as exc:
            log.warning("retention.table_failed", table=table, error=str(exc))
            result.errors.append(f"{table}: {exc}")

    try:
        async with SessionLocal() as session:
            result.audit_rows_deleted = await _expire_audit(
                session, settings.retention_audit_days, dry_run
            )
            if not dry_run:
                await session.commit()
    except Exception as exc:
        log.warning("retention.audit_failed", error=str(exc))
        result.errors.append(f"audit_log: {exc}")

    log.info(
        "retention.swept",
        dry_run=dry_run,
        chunks=result.chunks_dropped,
        audit_rows=result.audit_rows_deleted,
        errors=len(result.errors),
        detections_days=settings.retention_detections_days,
        audit_days=settings.retention_audit_days,
    )
    return result


async def evidence_expired(session: AsyncSession, alert: Alert) -> bool:
    """True when the detection behind an alert is past its retention date.

    Lets a screen say "the evidence has passed its retention date" instead of
    showing a lookup failure. The two look identical to a caller that only
    checks for a missing row, and they mean very different things: one is
    policy working, the other is a bug.
    """
    if alert.detection_id is None:
        return False
    if alert.created_at is None:
        return False
    if alert.created_at >= cutoff_for(settings.retention_detections_days):
        return False

    found = (
        await session.execute(
            select(func.count()).select_from(Detection).where(Detection.id == alert.detection_id)
        )
    ).scalar_one()
    return found == 0


class RetentionEnforcer:
    """Runs the sweep on a schedule."""

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self.last: SweepResult | None = None

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.last = await sweep(dry_run=not settings.retention_enforce)
            except Exception:
                log.warning("retention.sweep_failed", exc_info=True)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=SWEEP_INTERVAL_S)

    def start(self) -> None:
        if self._task is not None:
            return
        if not settings.retention_enforce:
            # Reported rather than silent. An operator reading the log must be
            # able to tell that retention is off, because the alternative is
            # believing a control is running when it is not.
            log.warning(
                "retention.disabled",
                detail=(
                    "RETENTION_ENFORCE is false; the sweep will report what it "
                    "would delete but delete nothing."
                ),
            )
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="retention")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None


enforcer = RetentionEnforcer()

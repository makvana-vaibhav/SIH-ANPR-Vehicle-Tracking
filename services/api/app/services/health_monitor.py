"""Fleet health monitoring.

Required by the challenge: Model 1 must provide "camera health monitoring"
(FAQ Q15). It is also the difference between a registry and an operational
system — knowing a camera *exists* is useless if nobody notices it stopped
working three weeks ago.

Design notes that matter at 80,000 cameras:

* **Probes are staggered**, not synchronised. Probing the whole estate at once
  would produce a thundering herd against every departmental VMS on a fixed
  cycle. Cameras are spread across the interval by a stable hash of their id,
  so the load is smooth and each camera keeps its own slot between restarts.
* **Concurrency is bounded** by a semaphore. Unbounded ``gather`` over 80,000
  cameras would exhaust file descriptors long before it exhausted the network.
* **Status changes require consecutive failures** (``HEALTH_OFFLINE_AFTER_FAILURES``).
  A single dropped packet must not page a control room.
* Every probe is written to the ``camera_health`` hypertable, which is what
  makes uptime sparklines and gap analysis possible.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.adapters import adapter_for
from app.adapters.base import INTEGRATION_ERRORS, NOT_INTEGRATED_ERRORS, HealthProbe
from app.core.config import settings
from app.core.logging import get_logger
from app.models.enums import AlertType, CameraStatus, Priority
from app.models.intelligence import Alert
from app.models.registry import Camera, CameraHealth

log = get_logger("api.health_monitor")

#: Cap on simultaneous probes. Tuned for a laptop; the scale profile raises it.
MAX_CONCURRENT_PROBES = 32

#: Consecutive-failure counters, keyed by camera id. In-memory by design —
#: this is debounce state, not a fact worth persisting, and it should reset
#: when the monitor restarts.
_failure_counts: dict[uuid.UUID, int] = defaultdict(int)
_success_counts: dict[uuid.UUID, int] = defaultdict(int)


async def probe_camera(
    session: AsyncSession, camera: Camera, *, record: bool = True
) -> HealthProbe:
    """Probe one camera through its VMS adapter and record the result."""
    adapter = adapter_for(camera.vms, timeout_seconds=settings.health_probe_timeout_seconds)

    try:
        probe = await adapter.probe_health(camera)
    except Exception as exc:
        # Supervisor boundary: one misbehaving adapter must not stop the sweep.
        log.error(
            "health.probe_raised",
            camera_code=camera.camera_code,
            adapter=adapter.adapter_type,
            error=str(exc),
            exc_info=True,
        )
        probe = HealthProbe(reachable=False, error_code="adapter_exception", detail=str(exc))
    finally:
        await adapter.close()

    if record:
        session.add(
            CameraHealth(
                camera_id=camera.id,
                ts=probe.probed_at,
                reachable=probe.reachable,
                fps_actual=probe.fps_actual,
                latency_ms=probe.latency_ms,
                bitrate_kbps=probe.bitrate_kbps,
                frame_drop_pct=probe.frame_drop_pct,
                error_code=probe.error_code,
            )
        )

    return probe


def _should_flip(camera_id: uuid.UUID, probe: HealthProbe, current: str) -> str | None:
    """Decide whether a camera's status should change.

    Debounced in both directions: a camera must fail N consecutive probes
    before being declared offline, and succeed once to come back. Failing
    slowly and recovering quickly is the right asymmetry for a control room —
    false alarms erode trust faster than a slightly delayed one.
    """
    threshold = settings.health_offline_after_failures
    new_status = probe.status.value

    # A camera that was delivering video and has stopped is a genuine outage,
    # even though the raw error ("not publishing") is the same one a
    # never-integrated camera reports. The difference is where it came FROM:
    # losing a working feed needs an operator; a camera that never had one does
    # not. Only the transition can tell them apart, which is why this lives
    # here and not in HealthProbe.status.
    if (
        not probe.reachable
        and probe.error_code in NOT_INTEGRATED_ERRORS
        and current in (CameraStatus.ONLINE.value, CameraStatus.DEGRADED.value)
    ):
        new_status = CameraStatus.OFFLINE.value

    if probe.reachable:
        _failure_counts.pop(camera_id, None)
        _success_counts[camera_id] += 1
        return new_status if new_status != current else None

    _success_counts.pop(camera_id, None)
    _failure_counts[camera_id] += 1

    if _failure_counts[camera_id] < threshold:
        # Not yet convinced; leave the status alone.
        return None
    return new_status if new_status != current else None


async def _raise_camera_down_alert(
    session: AsyncSession, camera: Camera, probe: HealthProbe
) -> None:
    """Raise a camera_down alert, unless one is already open.

    Deduplicated on open alerts so a camera that has been down for a week does
    not accumulate 20,000 identical alerts.
    """
    existing = await session.scalar(
        select(Alert).where(
            Alert.camera_id == camera.id,
            Alert.alert_type == AlertType.CAMERA_DOWN.value,
            Alert.status.in_(("new", "acknowledged", "dispatched")),
        )
    )
    if existing is not None:
        return

    # An ANPR camera going dark is an intelligence gap, not just a maintenance
    # ticket — it is a hole in the route history the correlator can build.
    priority = Priority.HIGH if camera.anpr_enabled else Priority.MEDIUM

    session.add(
        Alert(
            camera_id=camera.id,
            alert_type=AlertType.CAMERA_DOWN.value,
            priority=priority.value,
            status="new",
            notes=(
                f"{camera.camera_code} ({camera.name}) unreachable: "
                f"{probe.error_code or 'unknown error'}"
                f"{f' — {probe.detail}' if probe.detail else ''}"
            ),
        )
    )
    log.warning(
        "health.camera_down",
        camera_code=camera.camera_code,
        error_code=probe.error_code,
        anpr=camera.anpr_enabled,
    )


async def _raise_integration_alerts(session: AsyncSession, failures: dict[uuid.UUID, int]) -> int:
    """Raise one alert per unreachable VMS, however many cameras it holds.

    A dead Milestone server means 4,000 cameras are unreadable, but it is ONE
    incident with one fix. Alerting per camera would make the alert feed
    useless at exactly the moment an operator needs it.
    """
    from app.models.registry import VmsInstance

    raised = 0
    for vms_id, affected in failures.items():
        existing = await session.scalar(
            select(Alert).where(
                Alert.alert_type == AlertType.CAMERA_DOWN.value,
                Alert.status.in_(("new", "acknowledged", "dispatched")),
                Alert.notes.like(f"%vms:{vms_id}%"),
            )
        )
        if existing is not None:
            continue

        vms = await session.scalar(select(VmsInstance).where(VmsInstance.id == vms_id))
        name = vms.name if vms else str(vms_id)

        session.add(
            Alert(
                alert_type=AlertType.CAMERA_DOWN.value,
                priority=Priority.HIGH.value if affected > 25 else Priority.MEDIUM.value,
                status="new",
                notes=(
                    f"Integration unreachable: {name} — {affected} cameras cannot "
                    f"be reached through this VMS. [vms:{vms_id}]"
                ),
            )
        )
        log.warning("health.integration_down", vms=name, affected_cameras=affected)
        raised += 1
    return raised


async def sweep(
    session: AsyncSession, *, camera_ids: list[uuid.UUID] | None = None
) -> dict[str, int]:
    """Probe the fleet once and apply status transitions.

    Returns a summary of what changed, which the monitor logs and the tests
    assert against.
    """
    stmt = select(Camera).options(selectinload(Camera.vms))
    if camera_ids:
        stmt = stmt.where(Camera.id.in_(camera_ids))

    cameras = list((await session.scalars(stmt)).all())
    if not cameras:
        return {"probed": 0, "online": 0, "offline": 0, "changed": 0, "alerts": 0}

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_PROBES)

    async def guarded(camera: Camera) -> tuple[Camera, HealthProbe]:
        async with semaphore:
            return camera, await probe_camera(session, camera)

    results = await asyncio.gather(*(guarded(c) for c in cameras))

    online = offline = changed = alerts = 0
    integration_failures: dict[uuid.UUID, int] = {}

    for camera, probe in results:
        if probe.reachable:
            online += 1
        else:
            offline += 1

        new_status = _should_flip(camera.id, probe, camera.status)
        if new_status is None:
            continue

        previous = camera.status
        camera.status = new_status
        camera.updated_at = datetime.now(UTC)
        changed += 1

        log.info(
            "health.status_changed",
            camera_code=camera.camera_code,
            previous=previous,
            current=new_status,
            error_code=probe.error_code,
        )

        if new_status == CameraStatus.OFFLINE.value:
            await _raise_camera_down_alert(session, camera, probe)
            alerts += 1
        elif probe.error_code in INTEGRATION_ERRORS and camera.vms_id is not None:
            # An unreachable VMS affects every camera behind it. Raising one
            # alert per camera would bury a control room in hundreds of
            # notifications for a single root cause, so integration failures
            # are aggregated per VMS instance below.
            integration_failures[camera.vms_id] = integration_failures.get(camera.vms_id, 0) + 1

    alerts += await _raise_integration_alerts(session, integration_failures)

    await session.commit()

    summary = {
        "probed": len(cameras),
        "online": online,
        "offline": offline,
        "changed": changed,
        "alerts": alerts,
    }
    log.info("health.sweep_complete", **summary)
    return summary


def stagger_offset(camera_id: uuid.UUID, interval_seconds: int) -> float:
    """Stable position for a camera within the probe interval.

    Derived from the camera's own id, so a camera keeps the same slot across
    restarts and the load stays evenly spread rather than re-clumping.
    """
    return (camera_id.int % (interval_seconds * 1000)) / 1000.0

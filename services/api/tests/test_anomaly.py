"""Trajectory anomaly detection — app/services/anomaly.py, its wiring into
route_history.snapshot(), and the alert it raises.

Three layers, tested separately because a bug in any one of them would be
invisible from the others:

* **Scoring** (`evaluate_new_hops` / `_score_leg`) — pure logic over
  hand-built `Route`/`Hop` objects plus real `vehicle_tracks` rows for the
  baseline. No detections needed here at all.
* **Raising** (`alerts.raise_for_anomaly`) — that a scored list of reasons
  becomes a real, explainable `Alert` row with the right priority, and dedupes
  the way a watchlist hit does.
* **Wiring** (`route_history.snapshot`) — that a genuinely new journey with a
  genuinely rare leg produces an ANOMALY alert end to end, through the same
  path the ingest daemon runs. This is the layer most likely to hide an
  ordering bug: baselines must be read *before* the current advance is
  persisted, or a journey's own newest leg inflates the history it is being
  measured against.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from app.db.session import SessionLocal
from app.models.enums import AlertType, Priority
from app.models.intelligence import Detection, VehicleTrack
from app.services import alerts as alerts_service
from app.services import anomaly, route_history
from app.services.correlator import Hop, Route

pytestmark = pytest.mark.integration

# A fixed past window, isolated from the live simulator the same way
# test_analytics.py isolates the analytics endpoints — nothing a real
# vehicle produces will ever be timestamped here.
WINDOW_START = datetime(2024, 3, 4, 8, 0, 0, tzinfo=UTC)

PLATE_PREFIX = "ZZANOMALY"


def _plate(suffix: str) -> str:
    return f"{PLATE_PREFIX}{suffix}"


def _hop(
    camera_code: str,
    arrived_at: datetime,
    *,
    camera_id: uuid.UUID | None = None,
    elapsed_s: float | None = 300.0,
    implied_kmph: float | None = 36.0,
    plausible: bool = True,
) -> Hop:
    """A `Hop` built directly, the way `correlator.score_legs` would leave
    one — the anomaly scorer only ever sees these, never raw detections."""
    return Hop(
        camera_id=camera_id or uuid.uuid4(),
        camera_code=camera_code,
        camera_name=camera_code,
        city=None,
        district=None,
        lat=23.0,
        lon=72.5,
        arrived_at=arrived_at,
        departed_at=arrived_at,
        sightings=1,
        best_confidence=0.9,
        elapsed_s=elapsed_s,
        implied_kmph=implied_kmph,
        flags=[] if plausible else ["implausible_speed"],
    )


def _hop_json(camera_code: str, arrived_at: datetime, *, elapsed_s: float, plausible: bool = True) -> dict:
    return {
        "camera_code": camera_code,
        "arrived_at": arrived_at.isoformat(),
        "distance_m": 3_000.0,
        "elapsed_s": elapsed_s,
        "implied_kmph": (3_000.0 / elapsed_s) * 3.6 if elapsed_s else None,
        "plausible": plausible,
    }


async def _insert_track(plate: str, created_at: datetime, hops: list[dict]) -> None:
    async with SessionLocal() as session:
        session.add(
            VehicleTrack(
                id=uuid.uuid4(),
                plate_normalised=plate,
                hops={"hops": hops},
                hop_count=len(hops),
                created_at=created_at,
            )
        )
        await session.commit()


async def _baseline_track(index: int, a: str, b: str, arrived: datetime, elapsed_s: float) -> None:
    """One prior vehicle's A->B leg, for the history `evaluate_new_hops` reads."""
    await _insert_track(
        _plate(f"BASE{index}"),
        created_at=arrived,
        hops=[_hop_json(a, arrived - timedelta(minutes=5), elapsed_s=300.0), _hop_json(b, arrived, elapsed_s=elapsed_s)],
    )


async def _cleanup() -> None:
    # Three explicit statements rather than a loop over table names: an
    # f-string building a table name into SQL is a lint-flagged shape
    # (ruff S608) even when, as here, the values are a hardcoded tuple and
    # never user input — the fix is to not have the shape at all.
    async with SessionLocal() as session:
        like = f"{PLATE_PREFIX}%"
        await session.execute(text("DELETE FROM alerts WHERE plate_normalised LIKE :p"), {"p": like})
        await session.execute(
            text("DELETE FROM vehicle_tracks WHERE plate_normalised LIKE :p"), {"p": like}
        )
        await session.execute(
            text("DELETE FROM detections WHERE plate_normalised LIKE :p"), {"p": like}
        )
        await session.commit()


@pytest.fixture(autouse=True)
async def _clean_synthetic_rows():
    await _cleanup()
    alerts_service.deduper.clear()
    yield
    await _cleanup()
    alerts_service.deduper.clear()


@pytest.fixture
async def cameras() -> list[dict]:
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                text("SELECT id, camera_code FROM cameras ORDER BY camera_code LIMIT 3")
            )
        ).mappings()
        result = [dict(r) for r in rows]
    if len(result) < 3:
        pytest.skip("fewer than 3 cameras seeded — run scripts/seed.py")
    return result


class TestScoring:
    """Pure scoring: hand-built hops, real `vehicle_tracks` for the baseline."""

    async def test_rare_transition_with_no_prior_history(self) -> None:
        arrived = WINDOW_START + timedelta(minutes=20)
        route = Route(
            plate=_plate("RARE"),
            hops=[_hop("CAM-A", arrived - timedelta(minutes=5)), _hop("CAM-B", arrived)],
            window_from=None,
            window_to=None,
        )

        reasons = await self._evaluate(route, route.hops, now=arrived)

        assert len(reasons) == 1, "the first hop in a route has no leg to score"
        assert reasons[0].factor == "rare_transition"
        assert "0 vehicle" in reasons[0].detail

    async def test_not_rare_once_three_distinct_vehicles_have_made_it(self) -> None:
        arrived = WINDOW_START + timedelta(minutes=20)
        for i in range(3):
            await _baseline_track(i, "CAM-A", "CAM-B", arrived - timedelta(days=i + 1), elapsed_s=300.0)

        route = Route(
            plate=_plate("COMMON"),
            hops=[_hop("CAM-A", arrived - timedelta(minutes=5)), _hop("CAM-B", arrived, elapsed_s=300.0)],
            window_from=None,
            window_to=None,
        )
        reasons = await self._evaluate(route, route.hops, now=arrived)

        assert not any(r.factor == "rare_transition" for r in reasons)

    async def test_slow_transition_against_the_median(self) -> None:
        arrived = WINDOW_START + timedelta(minutes=20)
        for i in range(5):
            await _baseline_track(i, "CAM-A", "CAM-B", arrived - timedelta(days=i + 1), elapsed_s=300.0)

        route = Route(
            plate=_plate("SLOW"),
            hops=[_hop("CAM-A", arrived - timedelta(minutes=12)), _hop("CAM-B", arrived, elapsed_s=700.0)],
            window_from=None,
            window_to=None,
        )
        reasons = await self._evaluate(route, route.hops, now=arrived)

        assert not any(r.factor == "rare_transition" for r in reasons), (
            "5 prior vehicles is enough history; this must not also read as rare"
        )
        slow = next(r for r in reasons if r.factor == "slow_transition")
        assert "700s" in slow.detail and "300s" in slow.detail

    async def test_fast_transition_against_the_median(self) -> None:
        arrived = WINDOW_START + timedelta(minutes=20)
        for i in range(5):
            await _baseline_track(i, "CAM-A", "CAM-B", arrived - timedelta(days=i + 1), elapsed_s=300.0)

        route = Route(
            plate=_plate("FAST"),
            hops=[_hop("CAM-A", arrived - timedelta(minutes=2)), _hop("CAM-B", arrived, elapsed_s=100.0)],
            window_from=None,
            window_to=None,
        )
        reasons = await self._evaluate(route, route.hops, now=arrived)

        assert any(r.factor == "fast_transition" for r in reasons)

    async def test_impossible_hop_short_circuits_other_factors(self) -> None:
        """No baseline exists either — which would normally also flag
        `rare_transition` — but an implausible leg is not additionally scored
        for rarity or duration: it is already the strongest finding."""
        arrived = WINDOW_START + timedelta(minutes=20)
        route = Route(
            plate=_plate("IMPOSSIBLE"),
            hops=[
                _hop("CAM-A", arrived - timedelta(seconds=2)),
                _hop("CAM-B", arrived, elapsed_s=2.0, implied_kmph=5_400.0, plausible=False),
            ],
            window_from=None,
            window_to=None,
        )
        reasons = await self._evaluate(route, route.hops, now=arrived)

        assert [r.factor for r in reasons] == ["impossible_hop"]

    async def test_only_new_hops_are_scored(self) -> None:
        """A->B already has five prior vehicles (an established route); B->C
        has none. Passing only the C hop as "new" must not re-flag A->B —
        that leg was already scored on a previous snapshot cycle."""
        arrived = WINDOW_START + timedelta(minutes=20)
        for i in range(5):
            await _baseline_track(i, "CAM-A", "CAM-B", arrived - timedelta(days=i + 1), elapsed_s=300.0)

        route = Route(
            plate=_plate("PARTIAL"),
            hops=[
                _hop("CAM-A", arrived - timedelta(minutes=10)),
                _hop("CAM-B", arrived - timedelta(minutes=5), elapsed_s=300.0),
                _hop("CAM-C", arrived, elapsed_s=200.0),
            ],
            window_from=None,
            window_to=None,
        )
        reasons = await self._evaluate(route, route.hops[-1:], now=arrived)

        assert len(reasons) == 1
        assert "CAM-B → CAM-C" in reasons[0].detail

    async def test_language_never_calls_the_vehicle_suspicious(self) -> None:
        """CLAUDE.md §1: a trajectory anomaly is a fact about the route, not
        an accusation about who drove it."""
        arrived = WINDOW_START + timedelta(minutes=20)
        route = Route(
            plate=_plate("LANGUAGE"),
            hops=[
                _hop("CAM-A", arrived - timedelta(seconds=2)),
                _hop("CAM-B", arrived, elapsed_s=2.0, implied_kmph=5_400.0, plausible=False),
            ],
            window_from=None,
            window_to=None,
        )
        reasons = await self._evaluate(route, route.hops, now=arrived)
        banned = ("suspect", "criminal", "wanted", "guilty")

        for reason in reasons:
            lowered = reason.detail.lower()
            assert not any(word in lowered for word in banned), reason.detail

    @staticmethod
    async def _evaluate(
        route: Route, new_hops: list[Hop], *, now: datetime
    ) -> list[anomaly.Reason]:
        async with SessionLocal() as session:
            return await anomaly.evaluate_new_hops(session, route, new_hops, now=now)


class TestRaiseForAnomaly:
    async def test_stores_reasons_and_sets_medium_priority(self, cameras: list[dict]) -> None:
        reasons = [anomaly.Reason("rare_transition", "CAM-A → CAM-B seen 0 vehicles")]
        async with SessionLocal() as session:
            alert = await alerts_service.raise_for_anomaly(
                session,
                plate=_plate("PRIORITY"),
                camera_id=cameras[0]["id"],
                camera_code=cameras[0]["camera_code"],
                arrived_at=datetime.now(UTC),
                reasons=reasons,
            )
            await session.commit()

        assert alert is not None
        assert alert.alert_type == AlertType.ANOMALY.value
        assert alert.priority == Priority.MEDIUM.value
        assert alert.reasons == [{"factor": "rare_transition", "detail": reasons[0].detail}]
        assert alert.watchlist_id is None
        assert alert.detection_id is None

    async def test_an_impossible_hop_raises_priority_to_high(self, cameras: list[dict]) -> None:
        reasons = [
            anomaly.Reason("rare_transition", "rare"),
            anomaly.Reason("impossible_hop", "impossible"),
        ]
        async with SessionLocal() as session:
            alert = await alerts_service.raise_for_anomaly(
                session,
                plate=_plate("HIGH"),
                camera_id=cameras[0]["id"],
                camera_code=cameras[0]["camera_code"],
                arrived_at=datetime.now(UTC),
                reasons=reasons,
            )
            await session.commit()

        assert alert is not None
        assert alert.priority == Priority.HIGH.value

    async def test_dedupes_like_a_watchlist_hit(self, cameras: list[dict]) -> None:
        plate = _plate("DEDUP")
        reasons = [anomaly.Reason("rare_transition", "rare")]
        async with SessionLocal() as session:
            first = await alerts_service.raise_for_anomaly(
                session,
                plate=plate,
                camera_id=cameras[0]["id"],
                camera_code=cameras[0]["camera_code"],
                arrived_at=datetime.now(UTC),
                reasons=reasons,
            )
            second = await alerts_service.raise_for_anomaly(
                session,
                plate=plate,
                camera_id=cameras[0]["id"],
                camera_code=cameras[0]["camera_code"],
                arrived_at=datetime.now(UTC),
                reasons=reasons,
            )
            await session.commit()

        assert first is not None
        assert second is None


class TestSnapshotWiring:
    """The full path: real detections -> route_history.snapshot ->
    alerts.raise_for_anomaly -> a committed, explainable Alert row.

    Uses real "now" timestamps rather than the fixed historical window the
    other classes use, because `route_history.snapshot`'s lookback is not
    parameterised — it always reads the last 6 hours. A synthetic plate
    prefix nobody else uses keeps this from colliding with whatever the
    simulator is doing concurrently.
    """

    async def test_a_fresh_two_camera_journey_raises_an_anomaly_alert(
        self, client: AsyncClient, auth_headers, cameras: list[dict]
    ) -> None:
        plate = _plate("WIRING")
        now = datetime.now(UTC)
        a, b = cameras[0], cameras[1]

        async with SessionLocal() as session:
            session.add(
                Detection(
                    id=uuid.uuid4(),
                    ts=now - timedelta(minutes=25),
                    camera_id=a["id"],
                    track_id=f"anomaly-test-{uuid.uuid4().hex[:8]}",
                    plate_normalised=plate,
                    plate_confidence=0.9,
                )
            )
            session.add(
                Detection(
                    id=uuid.uuid4(),
                    ts=now - timedelta(minutes=5),
                    camera_id=b["id"],
                    track_id=f"anomaly-test-{uuid.uuid4().hex[:8]}",
                    plate_normalised=plate,
                    plate_confidence=0.9,
                )
            )
            await session.commit()

        async with SessionLocal() as session:
            result = await route_history.snapshot(session)

        assert result["anomalies_raised"] >= 1
        mine = [p for p in result["raised_alerts"] if p["plate"] == plate]
        assert len(mine) == 1, "one new journey must raise exactly one alert, not one per leg"
        assert mine[0]["alert_type"] == AlertType.ANOMALY.value
        assert mine[0]["reasons"], "an anomaly alert with no stored reasons is the bug this phase exists to fix"

        # And it is genuinely persisted and explainable through the API an
        # operator actually uses, not only in the snapshot's return value.
        response = await client.get(
            "/api/v1/alerts",
            params={"open_only": "false", "limit": "50"},
            headers=await auth_headers("supervisor"),
        )
        assert response.status_code == 200
        api_alert = next(a for a in response.json()["items"] if a["plate_normalised"] == plate)
        assert api_alert["alert_type"] == "anomaly"
        assert api_alert["reasons"]
        assert all({"factor", "detail"} <= r.keys() for r in api_alert["reasons"])

    async def test_an_unchanged_journey_is_not_rescored(
        self, cameras: list[dict]
    ) -> None:
        plate = _plate("STABLE")
        now = datetime.now(UTC)
        a, b = cameras[0], cameras[1]

        async with SessionLocal() as session:
            session.add(
                Detection(
                    id=uuid.uuid4(),
                    ts=now - timedelta(minutes=25),
                    camera_id=a["id"],
                    track_id=f"anomaly-test-{uuid.uuid4().hex[:8]}",
                    plate_normalised=plate,
                    plate_confidence=0.9,
                )
            )
            session.add(
                Detection(
                    id=uuid.uuid4(),
                    ts=now - timedelta(minutes=5),
                    camera_id=b["id"],
                    track_id=f"anomaly-test-{uuid.uuid4().hex[:8]}",
                    plate_normalised=plate,
                    plate_confidence=0.9,
                )
            )
            await session.commit()

        async with SessionLocal() as session:
            await route_history.snapshot(session)
        alerts_service.deduper.clear()  # isolate the second call from the first's dedup memory

        async with SessionLocal() as session:
            second = await route_history.snapshot(session)

        mine = [p for p in second["raised_alerts"] if p["plate"] == plate]
        assert mine == [], "no new detection arrived, so there is nothing new to score"

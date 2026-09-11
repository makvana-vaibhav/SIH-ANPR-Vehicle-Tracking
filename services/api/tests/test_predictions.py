"""Predictive traffic — `GET /api/v1/predictions/congestion`.

Same fixed-historical-window isolation as `test_analytics.py`, for the same
reason: a live, concurrently-running demo cannot guarantee precise numeric
claims, so every test builds its own `detections` rows on a date
(2024-03-04) nothing else will ever touch, and hits the real Postgres the
router queries.

## Why the numbers below are exact, not approximate

Every scenario is built so `avg_bucket_flow == 1.0` for every hour involved:
three earlier days, one detection per 5-minute slot, per hour
(`_seed_baseline`). With that baseline, `index_pct` for a live bucket is
just `100 × vehicles-in-that-bucket` — no rounding surprises to chase — and
a live vehicle count that increases by exactly 1 per bucket
(`_seed_rising_live`) produces an *exactly* linear index sequence, so the
ordinary-least-squares fit `app.routers.predictions._fit_line` recovers its
slope and intercept with zero residual. That is what lets
`TestCongestionOk` assert specific forecast values and a near-zero backtest
MAE instead of just "some value came back".
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from app.db.session import SessionLocal
from app.models.intelligence import Detection

pytestmark = pytest.mark.integration

#: The instant every scenario forecasts "as of". On a 5-minute boundary so
#: every bucket in the 90-minute lookback aligns cleanly — `time_bucket`
#: aligns to absolute epoch, not to the query's own start.
AT = datetime(2024, 3, 4, 9, 0, 0, tzinfo=UTC)
LOOKBACK_START = AT - timedelta(minutes=90)  # 07:30 — spans hour 7 and hour 8

TRACK_PREFIX = "predictions-test-"


async def _insert_many(camera_id: uuid.UUID, timestamps: list[datetime]) -> None:
    async with SessionLocal() as session:
        for i, ts in enumerate(timestamps):
            session.add(
                Detection(
                    id=uuid.uuid4(),
                    ts=ts,
                    camera_id=camera_id,
                    track_id=f"{TRACK_PREFIX}{uuid.uuid4().hex[:8]}-{i}",
                )
            )
        await session.commit()


async def _cleanup() -> None:
    async with SessionLocal() as session:
        await session.execute(
            text("DELETE FROM detections WHERE track_id LIKE :p"), {"p": f"{TRACK_PREFIX}%"}
        )
        await session.commit()


@pytest.fixture(autouse=True)
async def _clean_synthetic_rows():
    await _cleanup()
    yield
    await _cleanup()


@pytest.fixture
async def cameras() -> list[dict]:
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT id, camera_code, corridor FROM cameras "
                    "WHERE corridor IS NOT NULL ORDER BY camera_code LIMIT 2"
                )
            )
        ).mappings()
        result = [dict(r) for r in rows]
    if len(result) < 2:
        pytest.skip("fewer than 2 corridor-tagged cameras seeded — run scripts/seed.py")
    return result


def _hour_slots(day: datetime, hours: tuple[int, ...]) -> list[datetime]:
    """One timestamp per 5-minute slot, for each hour, on one calendar day."""
    out: list[datetime] = []
    for hour in hours:
        base = day.replace(hour=hour, minute=0, second=0, microsecond=0)
        out.extend(base + timedelta(minutes=5 * i) for i in range(12))
    return out


async def _seed_baseline(camera_id: uuid.UUID, hours: tuple[int, ...]) -> None:
    """3 earlier days (2024-03-01..03), one detection per 5-minute slot in
    each hour. 12 vehicles/day × 3 days ÷ (3 days × 12 buckets/hour) == 1.0
    — every downstream assertion assumes exactly this."""
    timestamps: list[datetime] = []
    for days_ago in (1, 2, 3):
        day = (LOOKBACK_START - timedelta(days=days_ago)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        timestamps.extend(_hour_slots(day, hours))
    await _insert_many(camera_id, timestamps)


async def _seed_rising_live(camera_id: uuid.UUID, *, start_count: int = 2, buckets: int = 18) -> None:
    """All `buckets` consecutive 5-minute buckets of the lookback window,
    `start_count + i` vehicles in bucket `i` — a perfectly straight line in
    vehicle count, and (with `avg_bucket_flow == 1.0`) in index_pct too."""
    timestamps: list[datetime] = []
    for i in range(buckets):
        bucket_start = LOOKBACK_START + timedelta(minutes=5 * i)
        count = start_count + i
        timestamps.extend(bucket_start + timedelta(seconds=10 * j) for j in range(count))
    await _insert_many(camera_id, timestamps)


class TestCongestionOk:
    async def test_rising_trend_forecast_and_backtest(
        self, client: AsyncClient, auth_headers, cameras: list[dict]
    ) -> None:
        camera = cameras[0]
        await _seed_baseline(camera["id"], hours=(7, 8))
        await _seed_rising_live(camera["id"], start_count=2)

        response = await client.get(
            "/api/v1/predictions/congestion",
            params={"at": AT.isoformat()},
            headers=await auth_headers("analyst"),
        )
        assert response.status_code == 200, response.text
        body = response.json()

        assert body["window"]["bucket_seconds"] == 300
        assert body["baseline_window_days"] == 7
        assert body["horizons_minutes"] == [15, 30]

        entry = next(s for s in body["series"] if s["key"] == camera["camera_code"])

        # Bucket 17 (the last of 18): vehicles = 2 + 17 = 19, index = 1900.
        assert entry["status"] == "ok"
        assert entry["current_index_pct"] == pytest.approx(1900.0, abs=0.5)
        assert entry["baseline_days"] == 3
        assert entry["trend_samples"] == 6
        # 100 index points per 5-minute bucket == 20 points/minute, exactly,
        # since the whole 18-bucket sequence is perfectly linear.
        assert entry["trend_per_minute_pct"] == pytest.approx(20.0, abs=0.5)

        by_horizon = {f["horizon_minutes"]: f["index_pct"] for f in entry["forecasts"]}
        assert by_horizon[15] == pytest.approx(2300.0, abs=5.0)
        assert by_horizon[30] == pytest.approx(2600.0, abs=5.0)
        assert by_horizon[30] > by_horizon[15] > entry["current_index_pct"]

        assert any(f["factor"] == "inflow_trend" for f in entry["factors"])

        # A perfectly linear synthetic sequence: the backtest's own fit
        # should reconstruct it almost exactly.
        assert entry["backtest"]["status"] == "ok"
        assert entry["backtest"]["samples"] == 6
        assert entry["backtest"]["mae_pct"] < 1.0


class TestCongestionGroupedByCorridor:
    async def test_corridor_aggregates_its_only_seeded_camera(
        self, client: AsyncClient, auth_headers, cameras: list[dict]
    ) -> None:
        camera = cameras[0]
        await _seed_baseline(camera["id"], hours=(7, 8))
        await _seed_rising_live(camera["id"], start_count=2)

        response = await client.get(
            "/api/v1/predictions/congestion",
            params={"at": AT.isoformat(), "group_by": "corridor", "corridor": camera["corridor"]},
            headers=await auth_headers("analyst"),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["group_by"] == "corridor"

        entry = next(s for s in body["series"] if s["key"] == camera["corridor"])
        assert entry["status"] == "ok"
        # Nothing else touches this fixed historical window, so the
        # corridor total equals this one camera's own figure.
        assert entry["current_index_pct"] == pytest.approx(1900.0, abs=0.5)


class TestCongestionInsufficientData:
    async def test_too_few_live_buckets_for_a_trend(
        self, client: AsyncClient, auth_headers, cameras: list[dict]
    ) -> None:
        camera = cameras[1]
        await _seed_baseline(camera["id"], hours=(8,))
        # Only 2 of the 18 possible buckets — a trend line needs 3.
        for i in (16, 17):
            bucket_start = LOOKBACK_START + timedelta(minutes=5 * i)
            await _insert_many(camera["id"], [bucket_start] * 5)  # index == 500

        response = await client.get(
            "/api/v1/predictions/congestion",
            params={"at": AT.isoformat()},
            headers=await auth_headers("analyst"),
        )
        assert response.status_code == 200, response.text
        entry = next(
            s for s in response.json()["series"] if s["key"] == camera["camera_code"]
        )

        assert entry["status"] == "insufficient_data"
        # The most recent bucket's own index is still meaningful even though
        # there isn't enough history to trust a trend through it.
        assert entry["current_index_pct"] == pytest.approx(500.0, abs=0.5)
        assert all(f["index_pct"] is None for f in entry["forecasts"])
        assert entry["backtest"]["status"] == "insufficient_data"


class TestCongestionInsufficientHistory:
    async def test_no_baseline_reports_insufficient_history(
        self, client: AsyncClient, auth_headers, cameras: list[dict]
    ) -> None:
        camera = cameras[0]
        # A different hour of day than any other test in this file seeds,
        # with live detections but zero matching baseline rows.
        at = datetime(2024, 3, 4, 4, 0, 0, tzinfo=UTC)
        bucket_start = at - timedelta(minutes=90) + timedelta(minutes=25)
        await _insert_many(camera["id"], [bucket_start, bucket_start + timedelta(seconds=30)])

        response = await client.get(
            "/api/v1/predictions/congestion",
            params={"at": at.isoformat()},
            headers=await auth_headers("analyst"),
        )
        assert response.status_code == 200, response.text
        entry = next(
            s for s in response.json()["series"] if s["key"] == camera["camera_code"]
        )

        assert entry["status"] == "insufficient_history"
        assert entry["current_index_pct"] is None
        assert entry["baseline_days"] == 0
        assert all(f["index_pct"] is None for f in entry["forecasts"])


class TestAccessControl:
    async def test_unauthenticated_is_refused(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/predictions/congestion")
        assert response.status_code == 401

    @pytest.mark.parametrize("role", ["admin", "supervisor", "operator", "analyst", "auditor"])
    async def test_every_operational_role_can_read(
        self, client: AsyncClient, auth_headers, role: str
    ) -> None:
        """Same matrix as analytics (rbac.py): every role but `api_client`
        has `ANALYTICS_READ`, and a forecast identifies no one."""
        response = await client.get(
            "/api/v1/predictions/congestion", headers=await auth_headers(role)
        )
        assert response.status_code == 200, f"{role}: {response.text}"

    async def test_api_client_is_denied(self, client: AsyncClient, auth_headers) -> None:
        response = await client.get(
            "/api/v1/predictions/congestion", headers=await auth_headers("api_client")
        )
        assert response.status_code == 403

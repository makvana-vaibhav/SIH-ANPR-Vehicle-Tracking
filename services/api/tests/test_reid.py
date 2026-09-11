"""Vehicle re-identification — `GET /api/v1/reid/candidates`.

Same fixed-historical-window isolation as the rest of this session's test
files, for the same reason. The one thing specific to this file: every
scenario computes its candidate's timestamp from the **real** great-circle
distance between two seeded cameras (`correlator.haversine_m`) rather than a
guessed offset, so a test asserting "this candidate is plausible" or "this
candidate is excluded as too fast" is true regardless of which two cameras
`make seed` happens to produce, and a test skips itself rather than
asserting something false if the chosen pair does not fit the scenario
(e.g. two cameras far enough apart that a 60 km/h assumption would not fit
inside the module's own `MAX_TIME_GAP`).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from app.db.session import SessionLocal
from app.models.intelligence import Detection
from app.services.correlator import MAX_PLAUSIBLE_KMPH, haversine_m

pytestmark = pytest.mark.integration

WINDOW_START = datetime(2024, 3, 4, 8, 0, 0, tzinfo=UTC)
TRACK_PREFIX = "reid-test-"

#: A safely-plausible urban speed to derive a "these two sightings could be
#: the same vehicle" time gap from real camera distance. Comfortably under
#: the correlator's 150 km/h ceiling with margin for float rounding.
PLAUSIBLE_KMPH = 60.0


async def _insert(
    *,
    camera_id: uuid.UUID,
    ts: datetime,
    vehicle_type: str | None = "car",
    embedding: list[float] | None = None,
    suffix: str = "",
) -> uuid.UUID:
    detection_id = uuid.uuid4()
    async with SessionLocal() as session:
        session.add(
            Detection(
                id=detection_id,
                ts=ts,
                camera_id=camera_id,
                track_id=f"{TRACK_PREFIX}{uuid.uuid4().hex[:8]}{suffix}",
                vehicle_type=vehicle_type,
                appearance_embedding=embedding,
            )
        )
        await session.commit()
    return detection_id


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
                    "SELECT id, camera_code, "
                    "ST_Y(location::geometry) AS lat, ST_X(location::geometry) AS lon "
                    "FROM cameras ORDER BY camera_code LIMIT 2"
                )
            )
        ).mappings()
        result = [dict(r) for r in rows]
    if len(result) < 2:
        pytest.skip("fewer than 2 cameras seeded — run scripts/seed.py")
    return result


def _plausible_gap_seconds(cam_a: dict, cam_b: dict) -> float:
    """Seconds apart that implies `PLAUSIBLE_KMPH` between these two real
    cameras. Skips the calling test if that gap would not fit inside the
    module's own 30-minute candidate window."""
    distance_km = haversine_m(cam_a["lat"], cam_a["lon"], cam_b["lat"], cam_b["lon"]) / 1000.0
    elapsed_s = distance_km / PLAUSIBLE_KMPH * 3600.0
    if elapsed_s >= 1700:
        pytest.skip("chosen cameras too far apart for this test's time window")
    return elapsed_s


class TestPlausibilityOnlyRanking:
    async def test_no_embeddings_ranks_by_plausibility(
        self, client: AsyncClient, auth_headers, cameras: list[dict]
    ) -> None:
        cam_a, cam_b = cameras
        gap = _plausible_gap_seconds(cam_a, cam_b)

        query_id = await _insert(camera_id=cam_a["id"], ts=WINDOW_START, suffix="q1")
        candidate_id = await _insert(
            camera_id=cam_b["id"], ts=WINDOW_START + timedelta(seconds=gap), suffix="c1"
        )

        response = await client.get(
            "/api/v1/reid/candidates",
            params={"detection_id": str(query_id), "detection_ts": WINDOW_START.isoformat()},
            headers=await auth_headers("analyst"),
        )
        assert response.status_code == 200, response.text
        body = response.json()

        assert body["found"] is True
        assert body["embedding_available"] is False

        ids = [c["detection_id"] for c in body["candidates"]]
        assert str(candidate_id) in ids
        candidate = next(c for c in body["candidates"] if c["detection_id"] == str(candidate_id))
        assert candidate["similarity"] is None
        assert 0.0 <= candidate["plausibility_score"] <= 1.0
        assert any(f["factor"] == "spatiotemporal_plausibility" for f in candidate["matched_on"])


class TestImplausibleExcluded:
    async def test_a_hop_too_fast_to_be_the_same_vehicle_is_excluded(
        self, client: AsyncClient, auth_headers, cameras: list[dict]
    ) -> None:
        cam_a, cam_b = cameras
        distance_km = haversine_m(cam_a["lat"], cam_a["lon"], cam_b["lat"], cam_b["lon"]) / 1000.0
        if distance_km <= 0:
            pytest.skip("chosen cameras share a location")
        # Implies exactly twice the correlator's own plausibility ceiling.
        gap = max(distance_km / (2 * MAX_PLAUSIBLE_KMPH) * 3600.0, 0.1)

        query_id = await _insert(camera_id=cam_a["id"], ts=WINDOW_START, suffix="q2")
        too_fast_id = await _insert(
            camera_id=cam_b["id"], ts=WINDOW_START + timedelta(seconds=gap), suffix="c2"
        )

        response = await client.get(
            "/api/v1/reid/candidates",
            params={"detection_id": str(query_id), "detection_ts": WINDOW_START.isoformat()},
            headers=await auth_headers("analyst"),
        )
        assert response.status_code == 200, response.text
        ids = [c["detection_id"] for c in response.json()["candidates"]]
        assert str(too_fast_id) not in ids


class TestSameCameraExcluded:
    async def test_a_same_camera_sighting_is_not_a_reid_candidate(
        self, client: AsyncClient, auth_headers, cameras: list[dict]
    ) -> None:
        """Another sighting at the query's own camera is not cross-camera
        re-identification — that is ordinary same-camera tracking."""
        camera = cameras[0]
        query_id = await _insert(camera_id=camera["id"], ts=WINDOW_START, suffix="q3")
        same_camera_id = await _insert(
            camera_id=camera["id"], ts=WINDOW_START + timedelta(minutes=1), suffix="c3"
        )

        response = await client.get(
            "/api/v1/reid/candidates",
            params={"detection_id": str(query_id), "detection_ts": WINDOW_START.isoformat()},
            headers=await auth_headers("analyst"),
        )
        assert response.status_code == 200, response.text
        ids = [c["detection_id"] for c in response.json()["candidates"]]
        assert str(same_camera_id) not in ids


class TestAppearanceRanking:
    async def test_similar_embedding_ranks_first_dissimilar_is_excluded(
        self, client: AsyncClient, auth_headers, cameras: list[dict]
    ) -> None:
        cam_a, cam_b = cameras
        gap = _plausible_gap_seconds(cam_a, cam_b)

        query_id = await _insert(
            camera_id=cam_a["id"], ts=WINDOW_START, embedding=[1.0, 0.0, 0.0], suffix="q4"
        )
        similar_id = await _insert(
            camera_id=cam_b["id"],
            ts=WINDOW_START + timedelta(seconds=gap),
            embedding=[1.0, 0.0, 0.0],
            suffix="sim",
        )
        dissimilar_id = await _insert(
            camera_id=cam_b["id"],
            ts=WINDOW_START + timedelta(seconds=gap + 30),
            embedding=[0.0, 1.0, 0.0],
            suffix="dis",
        )
        no_embedding_id = await _insert(
            camera_id=cam_b["id"],
            ts=WINDOW_START + timedelta(seconds=gap + 60),
            embedding=None,
            suffix="noemb",
        )

        response = await client.get(
            "/api/v1/reid/candidates",
            params={"detection_id": str(query_id), "detection_ts": WINDOW_START.isoformat()},
            headers=await auth_headers("analyst"),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["embedding_available"] is True

        ids = [c["detection_id"] for c in body["candidates"]]
        # Orthogonal vectors (cosine 0.0) sit below MIN_SIMILARITY — dropped,
        # not merely ranked low.
        assert str(dissimilar_id) not in ids
        assert str(similar_id) in ids
        # A candidate with no embedding still shows up via the
        # plausibility-only fallback — it just ranks behind one that was
        # actually matched on appearance.
        assert str(no_embedding_id) in ids
        assert ids.index(str(similar_id)) < ids.index(str(no_embedding_id))

        similar = next(c for c in body["candidates"] if c["detection_id"] == str(similar_id))
        assert similar["similarity"] == pytest.approx(1.0, abs=0.01)
        assert any(f["factor"] == "appearance_embedding" for f in similar["matched_on"])


class TestNotFound:
    async def test_unknown_detection_reports_not_found(
        self, client: AsyncClient, auth_headers
    ) -> None:
        response = await client.get(
            "/api/v1/reid/candidates",
            params={"detection_id": str(uuid.uuid4()), "detection_ts": WINDOW_START.isoformat()},
            headers=await auth_headers("analyst"),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["found"] is False
        assert body["candidates"] == []


class TestAccessControlAndAudit:
    async def test_unauthenticated_is_refused(self, client: AsyncClient) -> None:
        response = await client.get(
            "/api/v1/reid/candidates",
            params={"detection_id": str(uuid.uuid4()), "detection_ts": WINDOW_START.isoformat()},
        )
        assert response.status_code == 401

    async def test_an_auditor_may_not_search(self, client: AsyncClient, auth_headers) -> None:
        """Same matrix entry as `vehicles.search` (P8): search is withheld
        from the role that reviews searches."""
        response = await client.get(
            "/api/v1/reid/candidates",
            params={"detection_id": str(uuid.uuid4()), "detection_ts": WINDOW_START.isoformat()},
            headers=await auth_headers("auditor"),
        )
        assert response.status_code == 403

    async def test_a_lookup_is_recorded_in_the_audit_trail(
        self, client: AsyncClient, auth_headers, cameras: list[dict]
    ) -> None:
        from sqlalchemy import func, select

        from app.models.security import AuditLog

        query_id = await _insert(camera_id=cameras[0]["id"], ts=WINDOW_START, suffix="audit")
        headers = await auth_headers("analyst")

        async def audit_rows() -> int:
            async with SessionLocal() as session:
                return (
                    await session.execute(
                        select(func.count())
                        .select_from(AuditLog)
                        .where(
                            AuditLog.action == "search.plate",
                            AuditLog.resource_id == f"reid:{query_id}",
                        )
                    )
                ).scalar_one()

        before = await audit_rows()
        await client.get(
            "/api/v1/reid/candidates",
            params={"detection_id": str(query_id), "detection_ts": WINDOW_START.isoformat()},
            headers=headers,
        )
        assert await audit_rows() == before + 1

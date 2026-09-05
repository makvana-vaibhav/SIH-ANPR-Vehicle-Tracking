"""The five judge moments, executed headlessly.

`docs/DEMO_SCRIPT.md` describes what a judge is shown. This asserts the same
path still works, through the API a browser uses, so a change that breaks the
demonstration fails here rather than on stage.

It runs against a **live stack** (`make demo`) and is skipped when one is not
running — a red suite on a developer's laptop who simply has not started the
containers teaches people to ignore the suite.

What it deliberately does *not* do is assert that plates are read from the
organisers' grid. That depends on their gateway, the time of day and where
their cameras point, none of which are ours to control, and a test that fails
for those reasons is noise.
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest

API = os.environ.get("SENTINEL_API", "http://localhost:8000")
ADMIN_PW = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "Sentinel@2026")

pytestmark = pytest.mark.asyncio


def _stack_is_up() -> bool:
    try:
        return httpx.get(f"{API}/health", timeout=3).status_code == 200
    except httpx.HTTPError:
        return False


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not _stack_is_up(), reason="needs a running stack — `make demo`"),
]


@pytest.fixture
async def client():
    async with httpx.AsyncClient(base_url=API, timeout=30.0) as c:
        yield c


@pytest.fixture
async def admin(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": ADMIN_PW}
    )
    assert response.status_code == 200, "admin cannot sign in — has `make seed` run?"
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _since(minutes: int) -> str:
    """An ISO timestamp safe to drop into a query string.

    `isoformat()` yields `+00:00`, and an unencoded `+` in a query string is
    decoded as a space — so the API rejects it as a malformed datetime. `Z`
    means the same thing and survives the round trip, which is why the
    frontend's `toISOString()` never hit this.
    """
    stamp = datetime.now(UTC) - timedelta(minutes=minutes)
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")


class TestMomentOne:
    """250+ cameras on a Gujarat map, filterable by department and status."""

    async def test_the_fleet_is_on_the_map(self, client, admin):
        response = await client.get("/api/v1/cameras/geojson?limit=5000", headers=admin)
        assert response.status_code == 200
        features = response.json()["features"]
        assert len(features) > 100, f"only {len(features)} cameras — seed may not have run"

    async def test_every_camera_has_a_position(self, client, admin):
        """A camera without coordinates cannot be on the map at all."""
        payload = (
            await client.get("/api/v1/cameras/geojson?limit=5000", headers=admin)
        ).json()
        for feature in payload["features"][:50]:
            lon, lat = feature["geometry"]["coordinates"]
            # Gujarat's bounding box, roughly. Catches a swapped lat/lon, which
            # puts the whole fleet in the sea off Somalia.
            assert 68 < lon < 75, f"longitude {lon} is not in Gujarat"
            assert 20 < lat < 25, f"latitude {lat} is not in Gujarat"

    async def test_the_fleet_can_be_filtered(self, client, admin):
        response = await client.get("/api/v1/cameras/summary", headers=admin)
        assert response.status_code == 200
        summary = response.json()
        assert summary["total"] > 100
        assert summary["by_department"], "no departments to filter by"


class TestMomentTwo:
    """Plates read from moving traffic, in real time."""

    async def test_there_is_an_anpr_fleet(self, client, admin):
        summary = (await client.get("/api/v1/cameras/summary", headers=admin)).json()
        assert summary["anpr_enabled"] > 0, "no camera is being analysed"

    async def test_plates_are_being_read(self, client, admin):
        """The demonstration camera must be producing readings.

        Given up to 90 s, because a freshly started worker probes transports
        and connects before it reads anything.
        """
        deadline = time.monotonic() + 90
        total = 0
        while time.monotonic() < deadline:
            response = await client.get(
                f"/api/v1/detections?limit=1&since={_since(15)}", headers=admin
            )
            total = response.json()["total"]
            if total > 0:
                return
            time.sleep(5)
        pytest.fail(
            f"no plate reads in 15 minutes ({total} found). "
            "Check: docker compose logs ai-worker"
        )

    async def test_a_reading_carries_its_evidence(self, client, admin):
        """An operator must be able to judge a reading, not just trust it."""
        payload = (
            await client.get(f"/api/v1/detections?limit=1&since={_since(15)}", headers=admin)
        ).json()
        if not payload["items"]:
            pytest.skip("no detections yet")
        row = payload["items"][0]
        for field in ("plate_confidence", "grammar_valid", "camera_code", "reads_total"):
            assert field in row, f"a detection without {field} cannot be judged"


class TestMomentThree:
    """A watchlist hit fires by itself, as a red alert with the evidence."""

    async def test_a_watchlist_entry_raises_an_alert(self, client, admin):
        """The whole chain: read → match → alert, on real detections.

        A plate the pipeline is *actually reading* is chosen rather than a
        fixed one, so this tests the live chain rather than seeded data.
        """
        recent = (
            await client.get(
                f"/api/v1/detections?limit=5&since={_since(10)}&readable_only=true",
                headers=admin,
            )
        ).json()
        if not recent["items"]:
            pytest.skip("nothing being read right now")

        plate = recent["items"][0]["plate"]
        assert plate

        # Start from a known state.
        existing = (
            await client.get(f"/api/v1/watchlist?plate={plate}", headers=admin)
        ).json()
        for entry in existing if isinstance(existing, list) else []:
            if entry["plate_normalised"] == plate:
                await client.delete(f"/api/v1/watchlist/{entry['id']}", headers=admin)

        created = await client.post(
            "/api/v1/watchlist",
            headers=admin,
            json={
                "plate": plate,
                "category": "stolen",
                "priority": "critical",
                "case_ref": "FIR/E2E/JUDGE",
            },
        )
        assert created.status_code in (201, 409), created.text
        entry_id = created.json().get("id") if created.status_code == 201 else None

        try:
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                alerts = (
                    await client.get(
                        f"/api/v1/alerts?plate={plate}&limit=1", headers=admin
                    )
                ).json()
                if alerts["total"] > 0:
                    alert = alerts["items"][0]
                    assert alert["priority"] == "critical"
                    assert alert["alert_type"] == "watchlist_hit"
                    return
                time.sleep(5)
            pytest.fail(f"{plate} was watchlisted but raised no alert within 180 s")
        finally:
            if entry_id:
                await client.delete(f"/api/v1/watchlist/{entry_id}", headers=admin)

    async def test_an_alert_can_be_acknowledged(self, client, admin):
        alerts = (await client.get("/api/v1/alerts?limit=5", headers=admin)).json()
        target = next((a for a in alerts["items"] if a["status"] == "new"), None)
        if target is None:
            pytest.skip("no new alert to acknowledge")

        response = await client.post(
            f"/api/v1/alerts/{target['id']}/transition",
            headers=admin,
            json={"status": "acknowledged", "notes": "e2e"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "acknowledged"


class TestMomentFour:
    """A searched plate yields its sightings and a route."""

    async def test_a_plate_can_be_searched(self, client, admin):
        recent = (
            await client.get(
                f"/api/v1/detections?limit=1&since={_since(60)}&readable_only=true",
                headers=admin,
            )
        ).json()
        if not recent["items"]:
            pytest.skip("nothing read in the last hour")

        plate = recent["items"][0]["plate"]
        found = (
            await client.get(f"/api/v1/detections?plate={plate}&since={_since(1440)}",
                             headers=admin)
        ).json()
        assert found["total"] > 0, f"{plate} was read but cannot be searched"

    async def test_a_partial_plate_can_be_searched(self, client, admin):
        recent = (
            await client.get(
                f"/api/v1/detections?limit=1&since={_since(60)}&readable_only=true",
                headers=admin,
            )
        ).json()
        if not recent["items"]:
            pytest.skip("nothing read in the last hour")
        plate = recent["items"][0]["plate"]
        response = await client.get(
            f"/api/v1/detections?plate_prefix={plate[:4]}&since={_since(1440)}",
            headers=admin,
        )
        assert response.status_code == 200
        assert response.json()["total"] > 0

    async def test_a_route_is_returned_as_valid_geojson(self, client, admin):
        recent = (
            await client.get(
                f"/api/v1/detections?limit=1&since={_since(60)}&readable_only=true",
                headers=admin,
            )
        ).json()
        if not recent["items"]:
            pytest.skip("nothing read in the last hour")

        plate = recent["items"][0]["plate"]
        response = await client.get(
            f"/api/v1/vehicles/{plate}/route?format=geojson&since={_since(1440)}",
            headers=admin,
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["type"] == "FeatureCollection"
        for feature in payload["features"]:
            assert feature["type"] == "Feature"
            assert "geometry" in feature and "properties" in feature

    async def test_the_route_states_that_it_is_not_the_road(self, client, admin):
        """The claim the whole screen rests on must travel with the answer."""
        response = await client.get(
            f"/api/v1/vehicles/GJ03AB1234/route?since={_since(1440)}", headers=admin
        )
        assert response.status_code == 200
        assert "lower bound" in response.json()["geometry_note"]


class TestMomentFive:
    """The architecture claim, and the audit trail behind everything."""

    async def test_fleet_health_reports_real_numbers(self, client, admin):
        response = await client.get("/api/v1/health/fleet", headers=admin)
        assert response.status_code == 200
        health = response.json()
        assert health["total"] > 100
        # `unknown` is its own category: never-probed is not the same as
        # offline, and a dashboard that conflates them lies about the fleet.
        assert {"online", "offline", "unknown"} <= set(health)

    async def test_every_search_left_an_audit_row(self, client, admin):
        """The traceability story, demonstrated rather than asserted."""
        await client.get(
            f"/api/v1/detections?plate=GJ03AB1234&since={_since(60)}", headers=admin
        )
        trail = (
            await client.get("/api/v1/audit?action=search.&limit=5", headers=admin)
        ).json()
        assert trail["total"] > 0, "a plate search left no audit row"

    async def test_reading_the_audit_trail_is_itself_audited(self, client, admin):
        before = (await client.get("/api/v1/audit?action=audit.&limit=1", headers=admin)).json()
        await client.get("/api/v1/audit?limit=1", headers=admin)
        after = (await client.get("/api/v1/audit?action=audit.&limit=1", headers=admin)).json()
        assert after["total"] > before["total"], "a reviewer left no trace"


class TestTheInterfaceIsHonest:
    """Claims the demo makes that a judge could check."""

    async def test_roles_see_only_what_they_may_use(self, client):
        """An auditor is refused a vehicle trace, not merely hidden from it."""
        auth = await client.post(
            "/api/v1/auth/login", json={"username": "auditor", "password": ADMIN_PW}
        )
        if auth.status_code != 200:
            pytest.skip("auditor account not seeded")
        headers = {"Authorization": f"Bearer {auth.json()['access_token']}"}

        refused = await client.get("/api/v1/vehicles/GJ03AB1234/route", headers=headers)
        assert refused.status_code == 403, "an auditor must not be able to trace vehicles"

        allowed = await client.get("/api/v1/audit?limit=1", headers=headers)
        assert allowed.status_code == 200, "an auditor must be able to read the trail"

    async def test_the_grid_credentials_never_reach_a_client(self, client, admin):
        """The stream grant must not carry the federated grid's password."""
        cameras = (
            await client.get("/api/v1/cameras?limit=500", headers=admin)
        ).json()["items"]
        grid = next((c for c in cameras if c["camera_code"].startswith("SBX-")), None)
        if grid is None:
            pytest.skip("no federated camera registered")

        grant = await client.get(f"/api/v1/cameras/{grid['id']}/stream", headers=admin)
        assert grant.status_code == 200
        body = grant.text
        assert "***" in body or "@" not in body.split("rtsp://")[-1][:60], (
            "the stream grant appears to carry credentials in its RTSP URL"
        )

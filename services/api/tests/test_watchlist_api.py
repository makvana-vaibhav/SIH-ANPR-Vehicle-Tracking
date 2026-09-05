"""The watchlist HTTP surface: create, amend, retire, and who may do each.

These exercise the endpoints over HTTP rather than calling the service layer.
That distinction is the point: the matching logic in `test_watchlist_matching`
was fully covered while every write endpoint was returning 422, because the
handlers annotated the current user as the bare model instead of the
`Annotated[..., Depends(...)]` alias and FastAPI therefore expected it in the
request body. Nothing that only tested the service layer could see it.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import delete

from app.db.session import SessionLocal
from app.models.intelligence import Watchlist

pytestmark = pytest.mark.asyncio


def a_plate() -> str:
    """A plate no other test — and no earlier run — is using.

    The database is not reset between runs, so a small random space collides
    with a row left behind by a previous run and the "adding twice conflicts"
    test fails on its *first* insert. 10 hex characters makes that vanishingly
    unlikely without exceeding the 24-character column.
    """
    return f"GJ01{uuid.uuid4().hex[:10].upper()}"


@pytest.fixture(autouse=True)
async def _remove_test_entries():
    """Delete anything this module added, however it was added.

    Autouse and pattern-based rather than a per-call helper, because the
    failure is a test *forgetting* to register its cleanup — and 151 stray
    entries had accumulated in the demo database before anyone noticed. A
    sweep catches call sites that do not exist yet.
    """
    yield
    async with SessionLocal() as db:
        await db.execute(delete(Watchlist).where(Watchlist.plate_normalised.like("GJ01%")))
        await db.commit()


class TestWatchlistWrites:
    async def test_supervisor_can_add_a_plate(self, client: AsyncClient, auth_headers):
        headers = await auth_headers("supervisor")
        plate = a_plate()

        created = await client.post(
            "/api/v1/watchlist",
            headers=headers,
            json={
                "plate": plate,
                "category": "stolen",
                "priority": "critical",
                "case_ref": "FIR/2026/TEST/001",
                "remarks": "added by a test",
            },
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["plate_normalised"] == plate
        assert body["priority"] == "critical"
        assert body["active"] is True

        listed = await client.get("/api/v1/watchlist", headers=headers)
        assert listed.status_code == 200
        assert plate in {row["plate_normalised"] for row in listed.json()}

    async def test_plate_is_normalised_on_the_way_in(self, client: AsyncClient, auth_headers):
        headers = await auth_headers("supervisor")
        plate = a_plate()

        created = await client.post(
            "/api/v1/watchlist",
            headers=headers,
            json={
                "plate": f" {plate[:4].lower()}-{plate[4:]} ",
                "category": "stolen",
            },
        )
        assert created.status_code == 201, created.text
        assert created.json()["plate_normalised"] == plate

    async def test_adding_the_same_plate_twice_conflicts(self, client: AsyncClient, auth_headers):
        headers = await auth_headers("supervisor")
        plate = a_plate()
        payload = {"plate": plate, "category": "stolen"}

        first = await client.post("/api/v1/watchlist", headers=headers, json=payload)
        assert first.status_code == 201, first.text
        second = await client.post("/api/v1/watchlist", headers=headers, json=payload)
        assert second.status_code == 409

    async def test_priority_can_be_amended(self, client: AsyncClient, auth_headers):
        headers = await auth_headers("supervisor")
        created = await client.post(
            "/api/v1/watchlist",
            headers=headers,
            json={
                "plate": a_plate(),
                "category": "suspect",
                "priority": "medium",
            },
        )
        assert created.status_code == 201, created.text

        patched = await client.patch(
            f"/api/v1/watchlist/{created.json()['id']}",
            headers=headers,
            json={"priority": "critical", "remarks": "escalated"},
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["priority"] == "critical"

    async def test_a_plate_too_short_to_match_on_is_rejected(
        self, client: AsyncClient, auth_headers
    ):
        headers = await auth_headers("supervisor")
        response = await client.post(
            "/api/v1/watchlist", headers=headers, json={"plate": "GJ", "category": "stolen"}
        )
        assert response.status_code == 422


class TestWatchlistPermissions:
    async def test_an_operator_may_not_add_plates(self, client: AsyncClient, auth_headers):
        headers = await auth_headers("operator")
        response = await client.post(
            "/api/v1/watchlist", headers=headers, json={"plate": a_plate(), "category": "stolen"}
        )
        assert response.status_code == 403

    async def test_a_supervisor_may_not_delete(self, client: AsyncClient, auth_headers):
        """Watchlist history is evidence; supervisors retire entries, admins remove them."""
        supervisor = await auth_headers("supervisor")
        created = await client.post(
            "/api/v1/watchlist", headers=supervisor, json={"plate": a_plate(), "category": "stolen"}
        )
        assert created.status_code == 201, created.text

        refused = await client.delete(
            f"/api/v1/watchlist/{created.json()['id']}", headers=supervisor
        )
        assert refused.status_code == 403

    async def test_unauthenticated_requests_are_rejected(self, client: AsyncClient):
        assert (await client.get("/api/v1/watchlist")).status_code == 401
        assert (
            await client.post("/api/v1/watchlist", json={"plate": a_plate(), "category": "stolen"})
        ).status_code == 401


class TestReissuingARetiredEntry:
    """Retiring is the only removal this API offers, so re-adding must work.

    Deleting a watchlist entry deactivates it rather than removing it — a
    surveillance decision that can be erased cannot be reviewed. But the
    retired row still occupies the unique constraint on (plate, case_ref), so
    inserting alongside it raised an integrity error the operator saw as
    "Internal server error", with no path forward.
    """

    async def test_reissuing_under_the_same_case_reactivates(self, client, auth_headers):
        admin = await auth_headers("admin")
        plate = a_plate()
        body = {"plate": plate, "category": "stolen", "priority": "high",
                "case_ref": "FIR/REISSUE/0001"}

        first = await client.post("/api/v1/watchlist", headers=admin, json=body)
        assert first.status_code == 201, first.text
        entry_id = first.json()["id"]

        assert (
            await client.delete(f"/api/v1/watchlist/{entry_id}", headers=admin)
        ).status_code == 204

        again = await client.post("/api/v1/watchlist", headers=admin, json=body)
        assert again.status_code == 201, again.text
        # The same record, brought back — not a second row competing with it.
        assert again.json()["id"] == entry_id

        listed = (await client.get(f"/api/v1/watchlist?plate={plate}", headers=admin)).json()
        assert [e["id"] for e in listed if e["plate_normalised"] == plate] == [entry_id]

    async def test_an_active_duplicate_is_still_refused(self, client, auth_headers):
        """Reinstating a retired entry must not become a way to duplicate a live one."""
        admin = await auth_headers("admin")
        plate = a_plate()
        body = {"plate": plate, "category": "stolen", "priority": "high",
                "case_ref": "FIR/REISSUE/0002"}

        assert (
            await client.post("/api/v1/watchlist", headers=admin, json=body)
        ).status_code == 201
        clash = await client.post("/api/v1/watchlist", headers=admin, json=body)
        assert clash.status_code == 409


class TestRoutesAreWhereTheClientExpects:
    async def test_every_router_is_under_the_api_prefix(self, client: AsyncClient):
        """The browser reaches the API through nginx at /api/v1.

        A router mounted at the root is unreachable in the product even though
        it answers perfectly well in a test that addresses it directly, so the
        prefix is asserted rather than assumed. Only the container healthchecks,
        the scrape endpoint and the root document live outside it — those are
        infrastructure surfaces, not product surfaces, and versioning them with
        the API would mean a monitoring system breaks on an API version bump.
        """
        spec = (await client.get("/openapi.json")).json()
        outside = {path for path in spec["paths"] if not path.startswith("/api/v1")} - {
            "/",
            "/health",
            "/ready",
            "/metrics",
        }
        assert not outside, f"routes mounted outside /api/v1: {sorted(outside)}"

"""The RBAC matrix.

The matrix in ``app/core/rbac.py`` is the platform's authorisation contract.
This module asserts it against the specification table independently, so that a
careless edit to the matrix fails a test rather than silently granting an
analyst the ability to delete cameras.

Specification:

| Role       | Cameras | Streams | Watchlist | Alerts    | Search | Users | Audit |
|------------|---------|---------|-----------|-----------|--------|-------|-------|
| admin      | CRUD    | view    | CRUD      | all       | yes    | CRUD  | read  |
| supervisor | CRU     | view    | CRU       | ack/close | yes    | –     | read  |
| operator   | read    | view    | read      | ack       | yes    | –     | –     |
| analyst    | read    | –       | read      | read      | yes    | –     | –     |
| auditor    | read    | –       | read      | read      | –      | –     | read  |
| api_client | create  | –       | –         | –         | –      | –     | –     |
"""

from __future__ import annotations

import pytest

from app.core.rbac import ROLE_PERMISSIONS, Permission, permissions_for, role_has
from app.models.enums import Role

P = Permission

#: The specification table, transcribed. Anything not listed is denied.
EXPECTED: dict[Role, set[Permission]] = {
    Role.ADMIN: set(Permission),
    Role.SUPERVISOR: {
        P.CAMERA_CREATE,
        P.CAMERA_READ,
        P.CAMERA_UPDATE,
        P.STREAM_VIEW,
        P.WATCHLIST_CREATE,
        P.WATCHLIST_READ,
        P.WATCHLIST_UPDATE,
        P.ALERT_READ,
        P.ALERT_ACKNOWLEDGE,
        P.ALERT_DISPATCH,
        P.ALERT_CLOSE,
        P.SEARCH_EXECUTE,
        P.AUDIT_READ,
        P.ANALYTICS_READ,
    },
    Role.OPERATOR: {
        P.CAMERA_READ,
        P.STREAM_VIEW,
        P.WATCHLIST_READ,
        P.ALERT_READ,
        P.ALERT_ACKNOWLEDGE,
        P.SEARCH_EXECUTE,
        P.ANALYTICS_READ,
    },
    Role.ANALYST: {
        P.CAMERA_READ,
        P.WATCHLIST_READ,
        P.ALERT_READ,
        P.SEARCH_EXECUTE,
        P.ANALYTICS_READ,
    },
    Role.AUDITOR: {
        P.CAMERA_READ,
        P.WATCHLIST_READ,
        P.ALERT_READ,
        P.AUDIT_READ,
        P.ANALYTICS_READ,
    },
    Role.API_CLIENT: {P.CAMERA_CREATE},
}


class TestMatrixMatchesSpecification:
    @pytest.mark.parametrize("role", list(Role))
    def test_role_grants_exactly_the_specified_permissions(self, role: Role) -> None:
        assert permissions_for(role) == EXPECTED[role], (
            f"{role.value}: granted {sorted(p.value for p in permissions_for(role))}, "
            f"expected {sorted(p.value for p in EXPECTED[role])}"
        )

    def test_every_role_is_covered(self) -> None:
        """No role may be left out of the matrix — an unmapped role is a silent deny."""
        assert set(ROLE_PERMISSIONS) == set(Role)


class TestPrivilegeBoundaries:
    """The denials that matter most, asserted individually."""

    def test_only_admin_can_delete_cameras(self) -> None:
        for role in Role:
            assert role_has(role, P.CAMERA_DELETE) is (role is Role.ADMIN)

    def test_only_admin_administers_users(self) -> None:
        for role in Role:
            expected = role is Role.ADMIN
            assert role_has(role, P.USER_CREATE) is expected
            assert role_has(role, P.USER_DELETE) is expected

    def test_auditor_cannot_search_plates(self) -> None:
        """An auditor oversees plate searches; granting them search defeats that."""
        assert not role_has(Role.AUDITOR, P.SEARCH_EXECUTE)

    def test_auditor_cannot_view_live_video(self) -> None:
        assert not role_has(Role.AUDITOR, P.STREAM_VIEW)

    def test_analyst_cannot_view_live_video(self) -> None:
        """Analysts work over recorded detections, not live feeds."""
        assert not role_has(Role.ANALYST, P.STREAM_VIEW)

    def test_auditor_can_read_analytics(self) -> None:
        """Unlike search, analytics identifies nobody.

        It reports how many vehicles crossed a junction and how fast the road
        is moving, so there is no separation-of-duties reason to withhold it
        from the role that reviews plate searches — and an auditor looking at a
        surge of searches benefits from knowing whether the road was busy.
        """
        assert role_has(Role.AUDITOR, P.ANALYTICS_READ)
        assert not role_has(Role.AUDITOR, P.SEARCH_EXECUTE)

    def test_api_client_cannot_read_analytics(self) -> None:
        """A leaked onboarding key must not reveal traffic patterns either."""
        assert not role_has(Role.API_CLIENT, P.ANALYTICS_READ)

    def test_api_client_is_write_only_and_narrow(self) -> None:
        """A leaked onboarding key must expose nothing about the estate."""
        granted = permissions_for(Role.API_CLIENT)
        assert granted == {P.CAMERA_CREATE}
        assert P.CAMERA_READ not in granted
        assert P.SEARCH_EXECUTE not in granted
        assert P.ALERT_READ not in granted

    def test_operator_can_acknowledge_but_not_close_alerts(self) -> None:
        assert role_has(Role.OPERATOR, P.ALERT_ACKNOWLEDGE)
        assert not role_has(Role.OPERATOR, P.ALERT_CLOSE)

    def test_supervisor_cannot_delete_watchlist_entries(self) -> None:
        """Supervisors get CRU, not D: watchlist history is evidence."""
        assert role_has(Role.SUPERVISOR, P.WATCHLIST_UPDATE)
        assert not role_has(Role.SUPERVISOR, P.WATCHLIST_DELETE)

    def test_only_admin_supervisor_and_auditor_read_the_audit_trail(self) -> None:
        allowed = {Role.ADMIN, Role.SUPERVISOR, Role.AUDITOR}
        for role in Role:
            assert role_has(role, P.AUDIT_READ) is (role in allowed)


class TestUnknownRoles:
    def test_unknown_role_is_denied_everything(self) -> None:
        """Fail closed: a role we do not recognise gets no grants at all."""
        assert permissions_for("superuser") == frozenset()
        assert not role_has("", P.CAMERA_READ)

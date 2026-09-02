"""The integration layer.

These cover the seam that makes federation work: one interface, many vendors.
Most are pure unit tests over the normalisation logic, because that is where
multi-vendor integration actually goes wrong — every VMS disagrees about field
names, and being liberal in what we accept is the whole point.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.adapters import adapter_for, available_adapters
from app.adapters.base import (
    INTEGRATION_ERRORS,
    NOT_INTEGRATED_ERRORS,
    CameraAdapter,
    HealthProbe,
)
from app.adapters.onvif import OnvifAdapter
from app.adapters.rtsp import (
    RtspAdapter,
    _classify_ffprobe_error,
    _parse_bitrate,
    _parse_frame_rate,
)
from app.adapters.sandbox import (
    SentinelSandboxAdapter,
    _external_id_of,
    _extract_location,
    _extract_resolution,
)
from app.adapters.simulated import SimulatedVmsAdapter
from app.adapters.vendor import VendorVmsAdapter, as_bool, extract_list, pick
from app.models.enums import CameraStatus


class TestAdapterRegistry:
    """Resolving a VMS row to the class that can talk to it."""

    def test_every_adapter_type_is_registered(self) -> None:
        registered = available_adapters()
        assert set(registered) >= {"rtsp", "onvif", "vendor_api", "sentinel_sandbox", "simulated"}

    @pytest.mark.parametrize(
        ("adapter_type", "expected"),
        [
            ("rtsp", RtspAdapter),
            ("onvif", OnvifAdapter),
            ("vendor_api", VendorVmsAdapter),
            ("sentinel_sandbox", SentinelSandboxAdapter),
            ("simulated", SimulatedVmsAdapter),
        ],
    )
    def test_resolves_by_adapter_type(self, adapter_type: str, expected: type) -> None:
        vms = SimpleNamespace(
            id=uuid.uuid4(),
            name="Test VMS",
            adapter_type=adapter_type,
            base_url="http://vms.example",
            credentials_ref=None,
        )
        assert isinstance(adapter_for(vms), expected)

    def test_camera_without_a_vms_falls_back_to_rtsp(self) -> None:
        assert isinstance(adapter_for(None), RtspAdapter)

    def test_unknown_vendor_degrades_to_rtsp_rather_than_vanishing(self) -> None:
        """An unrecognised vendor string must not take a camera off the map.

        Falling back to the lowest common denominator that usually works is
        better than refusing to represent the camera at all.
        """
        vms = SimpleNamespace(
            id=uuid.uuid4(),
            name="Mystery VMS",
            adapter_type="some_new_vendor",
            base_url=None,
            credentials_ref=None,
        )
        assert isinstance(adapter_for(vms), RtspAdapter)

    def test_credentials_ref_is_passed_not_resolved_eagerly(self) -> None:
        """The adapter holds the pointer; resolution happens at connect time."""
        vms = SimpleNamespace(
            id=uuid.uuid4(),
            name="V",
            adapter_type="vendor_api",
            base_url="http://v",
            credentials_ref="vault://sentinel/vms/v",
        )
        adapter = adapter_for(vms)
        assert adapter.credentials_ref == "vault://sentinel/vms/v"

    def test_every_adapter_implements_the_contract(self) -> None:
        for adapter_type in available_adapters():
            vms = SimpleNamespace(
                id=uuid.uuid4(),
                name="x",
                adapter_type=adapter_type,
                base_url="http://x",
                credentials_ref=None,
            )
            adapter = adapter_for(vms)
            assert isinstance(adapter, CameraAdapter)
            for method in ("probe_health", "get_stream_url", "list_cameras", "get_recording"):
                assert callable(getattr(adapter, method))


class TestHealthProbeStatus:
    """Status semantics — the distinction that stops false dispatches."""

    def test_healthy_stream_is_online(self) -> None:
        assert HealthProbe(reachable=True, fps_actual=25.0).status is CameraStatus.ONLINE

    def test_low_frame_rate_is_degraded_not_online(self) -> None:
        """4 fps will produce poor ANPR; an operator must know before trusting it."""
        probe = HealthProbe(reachable=True, fps_actual=4.0)
        assert probe.status is CameraStatus.DEGRADED

    def test_heavy_frame_loss_is_degraded(self) -> None:
        assert (
            HealthProbe(reachable=True, fps_actual=25.0, frame_drop_pct=40.0).status
            is CameraStatus.DEGRADED
        )

    def test_high_latency_is_degraded(self) -> None:
        assert (
            HealthProbe(reachable=True, fps_actual=25.0, latency_ms=9000).status
            is CameraStatus.DEGRADED
        )

    def test_genuine_camera_failure_is_offline(self) -> None:
        assert (
            HealthProbe(reachable=False, error_code="invalid_stream").status is CameraStatus.OFFLINE
        )

    @pytest.mark.parametrize("error", sorted(INTEGRATION_ERRORS))
    def test_integration_failure_is_unknown_not_offline(self, error: str) -> None:
        """If we cannot reach the VMS, we do not know the camera's state.

        Reporting "offline" here would send an engineer to inspect a camera
        that is working perfectly, which is a real and recurring cost.
        """
        assert HealthProbe(reachable=False, error_code=error).status is CameraStatus.UNKNOWN

    @pytest.mark.parametrize("error", sorted(NOT_INTEGRATED_ERRORS))
    def test_never_integrated_is_unknown(self, error: str) -> None:
        """Under on-demand publishing most of the estate is not streaming.

        That is the designed behaviour, not a fault.
        """
        assert HealthProbe(reachable=False, error_code=error).status is CameraStatus.UNKNOWN


class TestRtspProbeParsing:
    """ffprobe output handling."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("25/1", 25.0), ("30000/1001", 29.97), ("0/0", None), ("N/A", None), (None, None)],
    )
    def test_frame_rate_rationals(self, raw: str | None, expected: float | None) -> None:
        assert _parse_frame_rate(raw) == expected

    def test_bitrate_converted_to_kbps(self) -> None:
        assert _parse_bitrate("4000000") == 4000
        assert _parse_bitrate(None) is None
        assert _parse_bitrate("not-a-number") is None

    @pytest.mark.parametrize(
        ("stderr", "code"),
        [
            ("401 Unauthorized", "unauthorized"),
            ("Server returned 404 Not Found", "stream_not_found"),
            ("Connection refused", "connection_refused"),
            ("No route to host", "unreachable"),
            ("Connection timed out", "timeout"),
            ("Invalid data found when processing input", "invalid_stream"),
            ("something entirely new", "probe_failed"),
        ],
    )
    def test_errors_classified_into_groupable_codes(self, stderr: str, code: str) -> None:
        """Grouping turns '31 cameras down' into 'one expired credential'."""
        assert _classify_ffprobe_error(stderr) == code

    async def test_missing_stream_url_is_reported_specifically(self) -> None:
        adapter = RtspAdapter(name="test")
        probe = await adapter.probe_health(SimpleNamespace(stream_url=None, sub_stream_url=None))
        assert probe.reachable is False
        assert probe.error_code == "no_stream_url"

    async def test_prefers_the_sub_stream_for_probing(self) -> None:
        """Analysing sub-streams instead of main streams is a large saving."""
        adapter = RtspAdapter(name="test")
        camera = SimpleNamespace(stream_url="rtsp://host/main", sub_stream_url="rtsp://host/sub")
        probe = await adapter.probe_health(camera)
        # ffprobe will fail against a fake host, but not with 'no_stream_url'.
        assert probe.error_code != "no_stream_url"


class TestVendorNormalisation:
    """One adapter covering four vendors depends on this being liberal."""

    @pytest.mark.parametrize("key", ["id", "camera_id", "cameraId", "guid", "deviceId", "uuid"])
    def test_identifier_found_under_any_vendor_spelling(self, key: str) -> None:
        assert pick({key: "CAM-1"}, "id") == "CAM-1"

    @pytest.mark.parametrize("key", ["name", "displayName", "camera_name", "title", "label"])
    def test_name_found_under_any_vendor_spelling(self, key: str) -> None:
        assert pick({key: "Gate Camera"}, "name") == "Gate Camera"

    def test_missing_concept_returns_none(self) -> None:
        assert pick({"unrelated": 1}, "id") is None

    def test_empty_string_treated_as_absent(self) -> None:
        assert pick({"id": "", "camera_id": "CAM-2"}, "id") == "CAM-2"

    @pytest.mark.parametrize(
        "payload",
        [
            [{"id": "1"}, {"id": "2"}],
            {"cameras": [{"id": "1"}, {"id": "2"}]},
            {"data": [{"id": "1"}, {"id": "2"}]},
            {"items": [{"id": "1"}, {"id": "2"}]},
            {"results": [{"id": "1"}, {"id": "2"}]},
            {"anything_else": [{"id": "1"}, {"id": "2"}]},
        ],
    )
    def test_camera_list_found_in_any_envelope(self, payload: object) -> None:
        assert len(extract_list(payload)) == 2

    def test_unparseable_payload_yields_empty_list(self) -> None:
        assert extract_list({"status": "ok"}) == []
        assert extract_list("nonsense") == []

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (True, True),
            (False, False),
            (1, True),
            (0, False),
            ("online", True),
            ("up", True),
            ("connected", True),
            ("live", True),
            ("offline", False),
            ("down", False),
            ("disconnected", False),
            ("maybe", None),
            (None, None),
        ],
    )
    def test_liveness_interpreted_across_vendor_vocabularies(
        self, value: object, expected: bool | None
    ) -> None:
        assert as_bool(value) is expected


class TestSandboxAdapter:
    """The challenge's own grid (sentinel.gujarat.gov.in)."""

    def _adapter(self) -> SentinelSandboxAdapter:
        return SentinelSandboxAdapter(
            name="Sentinel Sandbox Grid", base_url="https://sentinel.gujarat.gov.in"
        )

    def test_media_is_not_served_from_the_catalogue_host(self) -> None:
        """The mistake that cost weeks: RTSP built against the CDN hostname.

        A CDN terminates HTTP and cannot carry an RTSP session or a WebRTC
        media flow, which is why the organisers publish those on a direct
        address. Deriving all four URLs from one hostname produced
        `rtsp://<cdn>:8554/...`, the port looked closed, and the grid was
        written off as RTSP-blocked.
        """
        from app.core.config import settings

        endpoints = self._adapter()._endpoints_for("cam07")

        assert settings.sandbox_media_host in endpoints.rtsp
        assert settings.sandbox_media_host in endpoints.whep
        assert "sentinel.gujarat.gov.in" not in endpoints.rtsp
        assert "sentinel.gujarat.gov.in" not in endpoints.whep

    def test_the_catalogue_host_still_serves_hls(self) -> None:
        """HLS is the one that *is* behind the CDN."""
        endpoints = self._adapter()._endpoints_for("cam07")
        assert endpoints.hls == "https://sentinel.gujarat.gov.in/cam07/index.m3u8"

    def test_whep_is_plain_http_on_the_direct_host(self) -> None:
        """There is no certificate for a bare IP outside the CDN."""
        endpoints = self._adapter()._endpoints_for("cam07")
        assert endpoints.whep.startswith("http://")

    def test_our_gateway_uses_the_same_ports(self) -> None:
        """Their RTSP/WHEP ports match ours, so the grid is a drop-in source."""
        from app.adapters.sandbox import RTSP_PORT, WHEP_PORT

        assert (RTSP_PORT, WHEP_PORT) == (8554, 8889)

    def test_the_grid_id_is_read_from_the_stored_tag(self) -> None:
        """Their id format has changed once already (7 → cam07).

        The catalogue is the source of truth, so the id it gave us is stored
        and read back rather than derived from our own camera code.
        """
        camera = SimpleNamespace(
            camera_code="SBX-00007", tags=["sandbox", "grid-id:cam07", "transport:rtsp"]
        )
        assert _external_id_of(camera) == "cam07"

    def test_a_stored_id_wins_over_anything_derivable(self) -> None:
        camera = SimpleNamespace(camera_code="SBX-00007", tags=["grid-id:north-gate-3"])
        assert _external_id_of(camera) == "north-gate-3"

    @pytest.mark.parametrize(
        ("code", "expected"),
        [("SBX-00007", "7"), ("CAM-00034", "34"), ("12", "12")],
    )
    def test_untagged_cameras_fall_back_to_the_old_derivation(
        self, code: str, expected: str
    ) -> None:
        """Only for rows synced before the tag existed.

        It reproduces the *old* id format, which is wrong against the current
        grid — so this is a way to fail visibly, not a way to work.
        """
        assert _external_id_of(SimpleNamespace(camera_code=code, tags=[])) == expected

    @pytest.mark.parametrize(
        "item",
        [
            {"lat": 22.3, "lon": 70.8},
            {"latitude": 22.3, "longitude": 70.8},
            {"location": {"lat": 22.3, "lon": 70.8}},
            {"location": [70.8, 22.3]},  # GeoJSON order
            {"coordinates": [70.8, 22.3]},
        ],
    )
    def test_location_parsed_from_any_shape(self, item: dict) -> None:
        """The catalogue schema is not published, so parse defensively."""
        lat, lon = _extract_location(item)
        assert lat == pytest.approx(22.3)
        assert lon == pytest.approx(70.8)

    def test_missing_location_is_tolerated(self) -> None:
        assert _extract_location({"id": "1"}) == (None, None)

    @pytest.mark.parametrize(
        ("item", "expected"),
        [
            ({"resolution": "1920x1080"}, "1920x1080"),
            ({"width": 1280, "height": 720}, "1280x720"),
            ({"id": "1"}, None),
        ],
    )
    def test_resolution_normalised(self, item: dict, expected: str | None) -> None:
        assert _extract_resolution(item) == expected


class TestSecretsAreNeverInlined:
    """credentials_ref is a pointer, never a credential."""

    def test_unresolvable_reference_returns_none_not_raises(self) -> None:
        """A VMS awaiting credentials is normal during phased onboarding."""
        from app.services.secrets import resolve_credentials

        assert resolve_credentials(None) is None
        assert resolve_credentials("vault://sentinel/vms/absent") is None
        assert resolve_credentials("nonsense") is None

    def test_env_reference_resolves(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.services.secrets import resolve_credentials

        monkeypatch.setenv("TEST_VMS_USER", "svc-account")
        monkeypatch.setenv("TEST_VMS_PASS", "s3cret")
        creds = resolve_credentials("env://TEST_VMS_USER:TEST_VMS_PASS")

        assert creds is not None
        assert creds.username == "svc-account"

    def test_credentials_never_render_in_logs_or_tracebacks(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """repr is overridden so a password cannot leak into a log line."""
        from app.services.secrets import resolve_credentials

        monkeypatch.setenv("TEST_VMS_USER", "svc")
        monkeypatch.setenv("TEST_VMS_PASS", "hunter2")
        creds = resolve_credentials("env://TEST_VMS_USER:TEST_VMS_PASS")

        assert creds is not None
        assert "hunter2" not in repr(creds)
        assert "hunter2" not in str(creds)
        assert "***" in repr(creds)

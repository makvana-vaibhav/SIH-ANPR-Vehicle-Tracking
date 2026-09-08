"""Reading the fleet roster: transport choice, and what gets left out.

Two decisions live here and both are easy to get quietly wrong. Choosing RTSP
for a grid that blocks port 8554 means every camera opens, waits, and times
out — the fleet looks busy and reads nothing. Silently dropping a camera with
no usable URL means an operator believes it is being watched when it is not.
"""

from __future__ import annotations

import json

import pytest

from ai_worker.discovery import CameraStream, discover_registry

pytestmark = pytest.mark.asyncio


class FakeRedis:
    """Stands in for the roster key. The real client is created inside the
    function under test, so it is patched at the module boundary."""

    def __init__(self, payload: str | None) -> None:
        self.payload = payload

    async def get(self, _key: str) -> str | None:
        return self.payload

    async def aclose(self) -> None:
        return None


@pytest.fixture
def roster(monkeypatch):
    """Install a roster payload and a controllable RTSP probe."""

    def _install(cameras: list[dict], *, rtsp_ok: bool = True) -> dict[str, bool]:
        import redis.asyncio as aioredis

        monkeypatch.setattr(
            aioredis,
            "from_url",
            lambda *a, **k: FakeRedis(json.dumps({"cameras": cameras})),
        )

        async def probe(_host: str, _port: int = 8554, _timeout: float = 4.0) -> bool:
            return rtsp_ok

        monkeypatch.setattr("ai_worker.discovery.rtsp_reachable", probe)
        return {}

    return _install


def entry(code: str, **kw) -> dict:
    return {
        "camera_code": code,
        "label": kw.get("label", code),
        "rtsp_url": kw.get("rtsp_url", f"rtsp://gateway:8554/{code}"),
        "hls_url": kw.get("hls_url", f"https://gateway/live/{code}/index.m3u8"),
        "plate_regions": kw.get("plate_regions", ["IN"]),
    }


class TestTransportChoice:
    async def test_prefers_rtsp_when_the_port_is_open(self, roster) -> None:
        roster([entry("CAM-1")], rtsp_ok=True)
        streams = await discover_registry("redis://x", 0, 1)
        assert [s.transport for s in streams] == ["rtsp"]
        assert streams[0].rtsp_url.startswith("rtsp://")

    async def test_falls_back_to_hls_when_rtsp_is_blocked(self, roster) -> None:
        """The organisers' guide anticipates exactly this."""
        roster([entry("CAM-1")], rtsp_ok=False)
        streams = await discover_registry("redis://x", 0, 1)
        assert [s.transport for s in streams] == ["hls"]
        assert streams[0].rtsp_url.startswith("https://")

    async def test_the_probe_result_is_reused_across_cameras(self, roster) -> None:
        """One connect per host, not one per camera."""
        calls: list[str] = []
        roster([entry(f"CAM-{i}") for i in range(5)], rtsp_ok=False)
        probe: dict[str, bool] = {}

        import ai_worker.discovery as discovery

        original = discovery.rtsp_reachable

        async def counting(host: str, port: int = 8554, timeout: float = 4.0) -> bool:
            calls.append(host)
            return await original(host, port, timeout)

        discovery.rtsp_reachable = counting
        try:
            await discover_registry("redis://x", 0, 1, rtsp_probe=probe)
        finally:
            discovery.rtsp_reachable = original

        assert len(calls) == 1, "the same host must not be probed once per camera"

    async def test_hls_only_camera_needs_no_rtsp_probe(self, roster) -> None:
        roster([entry("CAM-1", rtsp_url="")], rtsp_ok=True)
        streams = await discover_registry("redis://x", 0, 1)
        assert [s.transport for s in streams] == ["hls"]


class TestWhatIsLeftOut:
    async def test_a_camera_with_no_usable_url_is_dropped(self, roster) -> None:
        roster([entry("CAM-1"), entry("CAM-2", rtsp_url="", hls_url="")], rtsp_ok=True)
        streams = await discover_registry("redis://x", 0, 1)
        assert [s.camera_code for s in streams] == ["CAM-1"]

    async def test_dropping_a_camera_is_reported(self, roster, caplog) -> None:
        """An operator must be told which cameras are not being watched."""
        roster([entry("CAM-2", rtsp_url="", hls_url="")], rtsp_ok=True)
        with caplog.at_level("WARNING"):
            await discover_registry("redis://x", 0, 1)
        assert any("CAM-2" in record.getMessage() for record in caplog.records)

    async def test_an_empty_roster_is_reported_not_silent(
        self, monkeypatch, caplog
    ) -> None:
        import redis.asyncio as aioredis

        monkeypatch.setattr(aioredis, "from_url", lambda *a, **k: FakeRedis(None))
        with caplog.at_level("WARNING"):
            streams = await discover_registry("redis://x", 0, 1)
        assert streams == []
        assert any("roster" in record.getMessage() for record in caplog.records)

    async def test_malformed_json_does_not_crash_the_worker(
        self, monkeypatch, caplog
    ) -> None:
        import redis.asyncio as aioredis

        monkeypatch.setattr(
            aioredis, "from_url", lambda *a, **k: FakeRedis("{not json")
        )
        with caplog.at_level("WARNING"):
            assert await discover_registry("redis://x", 0, 1) == []


class TestSharding:
    async def test_a_worker_takes_only_its_own_shard(self, roster) -> None:
        codes = [f"CAM-{i:03d}" for i in range(40)]
        roster([entry(c) for c in codes], rtsp_ok=True)

        first = {s.camera_code for s in await discover_registry("redis://x", 0, 3)}
        second = {s.camera_code for s in await discover_registry("redis://x", 1, 3)}
        third = {s.camera_code for s in await discover_registry("redis://x", 2, 3)}

        assert first | second | third == set(codes), "every camera must be owned"
        assert not (first & second) and not (second & third) and not (first & third)

    async def test_no_cap_is_applied_here(self, roster) -> None:
        """How many cameras run at once is the rotation's decision, not this one."""
        roster([entry(f"CAM-{i:03d}") for i in range(50)], rtsp_ok=True)
        streams = await discover_registry("redis://x", 0, 1)
        assert len(streams) == 50


class TestPlateRegions:
    async def test_regions_travel_with_the_camera(self, roster) -> None:
        roster([entry("CAM-DEMO", plate_regions=["IN", "GB"])], rtsp_ok=True)
        streams = await discover_registry("redis://x", 0, 1)
        assert streams[0].plate_regions == ("IN", "GB")

    async def test_a_camera_without_regions_defaults_to_indian(self, roster) -> None:
        """Widening the accepted formats must be deliberate, never accidental."""
        payload = entry("CAM-1")
        del payload["plate_regions"]
        roster([payload], rtsp_ok=True)
        streams = await discover_registry("redis://x", 0, 1)
        assert streams[0].plate_regions == ("IN",)

    @pytest.mark.filterwarnings("ignore")
    async def test_the_default_on_the_dataclass_is_indian(self) -> None:
        assert CameraStream(camera_code="X", rtsp_url="rtsp://h/x").plate_regions == (
            "IN",
        )

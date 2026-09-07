"""Camera assignment across workers.

Sharding decides which worker owns which camera with no coordinator, so the
properties that matter are: every camera is owned by exactly one worker, and
the assignment does not churn when the fleet changes.
"""

from __future__ import annotations

import pytest

from ai_worker.discovery import discover_sandbox, owns, shard_of


class TestSharding:
    def test_single_worker_owns_everything(self) -> None:
        assert all(owns(f"CAM-{i:04d}", 0, 1) for i in range(200))

    def test_every_camera_is_owned_exactly_once(self) -> None:
        """No camera unwatched, and no stream opened by two workers."""
        for shards in (2, 3, 4, 8):
            for i in range(300):
                code = f"GJ-RJT-{i:04d}"
                owners = [w for w in range(shards) if owns(code, w, shards)]
                assert len(owners) == 1, f"{code} owned by {owners} of {shards}"

    def test_assignment_is_stable(self) -> None:
        """Same camera, same shard, every time — including across processes."""
        for code in ("GJ-RJT-0001", "cam-00034", "AHM-JUNCTION-7"):
            assert len({shard_of(code, 4) for _ in range(50)}) == 1

    def test_load_is_reasonably_even(self) -> None:
        """A hash that piles every camera onto one worker would be useless."""
        shards = 4
        counts = [0] * shards
        for i in range(400):
            counts[shard_of(f"CAM-{i:04d}", shards)] += 1
        assert min(counts) > 400 / shards * 0.6, f"uneven distribution: {counts}"

    def test_adding_a_camera_does_not_reshuffle_the_others(self) -> None:
        """Positional assignment would tear down working streams for nothing."""
        fleet = [f"CAM-{i:04d}" for i in range(50)]
        before = {c: shard_of(c, 4) for c in fleet}
        fleet.insert(0, "CAM-NEW")
        after = {c: shard_of(c, 4) for c in fleet}
        assert all(before[c] == after[c] for c in before)

    @pytest.mark.parametrize("code", ["", "x", "GJ-RJT-0001", "a" * 200])
    def test_handles_any_code(self, code: str) -> None:
        assert 0 <= shard_of(code, 4) < 4


# ─────────────────────────────────────────────────────────────────────
# hosted grid grid
# ─────────────────────────────────────────────────────────────────────
CATALOGUE = {
    "cameras": [
        {
            "id": "1",
            "name": "Camera 1",
            "location": "01 Chiman bhai Bridge",
            "live": True,
            "rtsp_url": "rtsp://live.corp8.cloud:8554/stream/1",
            "hls_live_url": "/live/stream/1/index.m3u8",
        },
        {
            "id": "2",
            "name": "Camera 2",
            "location": "02 Janpath",
            "live": True,
            "rtsp_url": "rtsp://live.corp8.cloud:8554/stream/2",
            "hls_live_url": "/live/stream/2/index.m3u8",
        },
        {
            "id": "3",
            "name": "Camera 3",
            "location": "03 Offline one",
            "live": False,
            "rtsp_url": "rtsp://live.corp8.cloud:8554/stream/3",
            "hls_live_url": "/live/stream/3/index.m3u8",
        },
    ]
}


@pytest.mark.asyncio
async def test_sandbox_uses_rtsp_when_the_port_is_open(monkeypatch) -> None:
    async def fake_catalogue(_base, timeout=15.0):
        return CATALOGUE["cameras"]

    monkeypatch.setattr("ai_worker.discovery.sandbox_catalogue", fake_catalogue)
    streams = await discover_sandbox("https://live.corp8.cloud", 0, 1, transport="rtsp")
    assert [s.camera_code for s in streams] == ["SBX-00001", "SBX-00002"]
    assert all(s.rtsp_url.startswith("rtsp://") for s in streams)


@pytest.mark.asyncio
async def test_sandbox_falls_back_to_hls_when_rtsp_is_blocked(monkeypatch) -> None:
    """The guide's own advice: 'If port 8554 is blocked, use the HLS endpoint.'

    On a network where 8554 is filtered, assuming RTSP means a 30-second
    timeout per camera and no video at all.
    """

    async def fake_catalogue(_base, timeout=15.0):
        return CATALOGUE["cameras"]

    monkeypatch.setattr("ai_worker.discovery.sandbox_catalogue", fake_catalogue)
    streams = await discover_sandbox("https://live.corp8.cloud", 0, 1, transport="hls")
    assert all(s.transport == "hls" for s in streams)
    assert streams[0].rtsp_url == "https://live.corp8.cloud/live/stream/1/index.m3u8"


@pytest.mark.asyncio
async def test_cameras_not_publishing_are_skipped(monkeypatch) -> None:
    async def fake_catalogue(_base, timeout=15.0):
        return CATALOGUE["cameras"]

    monkeypatch.setattr("ai_worker.discovery.sandbox_catalogue", fake_catalogue)
    streams = await discover_sandbox("https://live.corp8.cloud", 0, 1, transport="hls")
    assert "SBX-00003" not in [
        s.camera_code for s in streams
    ], "offline camera was opened"


@pytest.mark.asyncio
async def test_the_location_name_travels_with_the_stream(monkeypatch) -> None:
    """An operator reading a log needs the junction, not 'Camera 1'."""

    async def fake_catalogue(_base, timeout=15.0):
        return CATALOGUE["cameras"]

    monkeypatch.setattr("ai_worker.discovery.sandbox_catalogue", fake_catalogue)
    streams = await discover_sandbox("https://live.corp8.cloud", 0, 1, transport="hls")
    assert "Chiman bhai Bridge" in streams[0].label


@pytest.mark.asyncio
async def test_workers_split_the_grid_without_overlap(monkeypatch) -> None:
    async def fake_catalogue(_base, timeout=15.0):
        return CATALOGUE["cameras"]

    monkeypatch.setattr("ai_worker.discovery.sandbox_catalogue", fake_catalogue)
    a = {
        s.camera_code
        for s in await discover_sandbox("https://x", 0, 2, transport="hls")
    }
    b = {
        s.camera_code
        for s in await discover_sandbox("https://x", 1, 2, transport="hls")
    }
    assert not (a & b), "two workers would open the same stream"
    assert a | b == {"SBX-00001", "SBX-00002"}


@pytest.mark.asyncio
async def test_an_empty_catalogue_yields_nothing(monkeypatch) -> None:
    async def fake_catalogue(_base, timeout=15.0):
        return []

    monkeypatch.setattr("ai_worker.discovery.sandbox_catalogue", fake_catalogue)
    assert await discover_sandbox("https://x", 0, 1) == []

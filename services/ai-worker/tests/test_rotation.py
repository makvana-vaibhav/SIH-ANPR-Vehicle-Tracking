"""Rotation: every camera gets a turn, and nobody is quietly starved.

The failure this guards against is silent. A worker that takes the first four
cameras and ignores the rest looks healthy — four streams running, events
flowing — while a vehicle passing camera 20 is never seen and never reported.
These tests pin the behaviour that makes coverage partial *in time*, which is
measurable, rather than partial *in space*, which is invisible.
"""

from __future__ import annotations

from ai_worker.discovery import CameraStream
from ai_worker.rotation import Rotation


def streams(*codes: str) -> list[CameraStream]:
    return [CameraStream(camera_code=c, rtsp_url=f"rtsp://host/{c}") for c in codes]


def rotation(
    slots: int = 2, slice_seconds: float = 30.0, pinned: tuple = ()
) -> Rotation:
    return Rotation(slots=slots, slice_seconds=slice_seconds, pinned=frozenset(pinned))


class TestFillingSlots:
    def test_starts_no_more_than_there_are_slots(self) -> None:
        r = rotation(slots=2)
        r.update_assignment(streams("A", "B", "C", "D"))
        assert len(r.next_up()) == 2

    def test_runs_everything_when_the_fleet_fits(self) -> None:
        r = rotation(slots=4)
        r.update_assignment(streams("A", "B"))
        assert {s.camera_code for s in r.next_up()} == {"A", "B"}

    def test_a_full_worker_starts_nothing_more(self) -> None:
        r = rotation(slots=2)
        r.update_assignment(streams("A", "B", "C"))
        for stream in r.next_up():
            r.take_slot(stream.camera_code)
        assert r.next_up() == []

    def test_a_released_slot_is_refilled(self) -> None:
        r = rotation(slots=2)
        r.update_assignment(streams("A", "B", "C"))
        for stream in r.next_up():
            r.take_slot(stream.camera_code)
        r.release_slot("A")
        assert [s.camera_code for s in r.next_up()] == ["C"]


class TestNobodyIsStarved:
    def test_a_full_sweep_reaches_every_camera(self) -> None:
        """The point of the whole design: camera 20 is not ignored for ever."""
        r = rotation(slots=2)
        fleet = streams(*[f"CAM-{i:02d}" for i in range(10)])
        r.update_assignment(fleet)

        seen: set[str] = set()
        # Ten rounds of "start what fits, then release it" must cover all ten.
        for _ in range(10):
            for stream in r.next_up():
                r.take_slot(stream.camera_code)
                seen.add(stream.camera_code)
            for code in list(r.started_at):
                r.release_slot(code)

        assert seen == {s.camera_code for s in fleet}

    def test_the_least_served_camera_goes_next(self) -> None:
        r = rotation(slots=1)
        r.update_assignment(streams("A", "B", "C"))
        r.served = {"A": 5, "B": 1, "C": 3}
        assert [s.camera_code for s in r.next_up()] == ["B"]

    def test_rotation_state_survives_a_fleet_change(self) -> None:
        r = rotation(slots=1)
        r.update_assignment(streams("A", "B"))
        r.take_slot("A")
        r.release_slot("A")
        r.update_assignment(streams("A", "B", "C"))
        # A has had its turn; it must not jump the queue again.
        assert r.served["A"] == 1
        assert [s.camera_code for s in r.next_up()] in (["B"], ["C"])

    def test_a_departed_camera_leaves_no_state_behind(self) -> None:
        r = rotation(slots=2)
        r.update_assignment(streams("A", "B"))
        r.take_slot("A")
        r.update_assignment(streams("B"))
        assert "A" not in r.started_at
        assert "A" not in r.served


class TestYielding:
    def test_a_camera_yields_once_its_slice_is_up(self) -> None:
        r = rotation(slots=1, slice_seconds=30.0)
        r.update_assignment(streams("A", "B"))
        r.take_slot("A", now=0.0)
        assert r.expired(now=31.0) == ["A"]

    def test_a_camera_holds_its_slot_until_the_slice_is_up(self) -> None:
        r = rotation(slots=1, slice_seconds=30.0)
        r.update_assignment(streams("A", "B"))
        r.take_slot("A", now=0.0)
        assert r.expired(now=29.0) == []

    def test_nothing_yields_when_nobody_is_waiting(self) -> None:
        """Rotating a camera out to replace it with nothing costs a reconnect."""
        r = rotation(slots=2, slice_seconds=30.0)
        r.update_assignment(streams("A"))
        r.take_slot("A", now=0.0)
        assert r.expired(now=999.0) == []

    def test_no_more_yield_than_there_are_cameras_waiting(self) -> None:
        r = rotation(slots=3, slice_seconds=30.0)
        r.update_assignment(streams("A", "B", "C", "D"))
        for code in ("A", "B", "C"):
            r.take_slot(code, now=0.0)
        # Only D is waiting, so only one slot should be given up.
        assert len(r.expired(now=31.0)) == 1

    def test_the_longest_running_camera_yields_first(self) -> None:
        r = rotation(slots=2, slice_seconds=10.0)
        r.update_assignment(streams("A", "B", "C"))
        r.take_slot("A", now=0.0)
        r.take_slot("B", now=5.0)
        assert r.expired(now=20.0) == ["A"]

    def test_rotation_can_be_switched_off(self) -> None:
        r = rotation(slots=1, slice_seconds=0.0)
        r.update_assignment(streams("A", "B"))
        r.take_slot("A", now=0.0)
        assert r.expired(now=10_000.0) == []
        assert r.slice_for("A") is None


class TestPinning:
    def test_a_pinned_camera_is_started_first(self) -> None:
        r = rotation(slots=1, pinned=("DEMO",))
        r.update_assignment(streams("A", "B", "DEMO"))
        assert [s.camera_code for s in r.next_up()] == ["DEMO"]

    def test_a_pinned_camera_never_yields(self) -> None:
        r = rotation(slots=2, slice_seconds=10.0, pinned=("DEMO",))
        r.update_assignment(streams("A", "B", "DEMO"))
        r.take_slot("DEMO", now=0.0)
        r.take_slot("A", now=0.0)
        assert r.expired(now=100.0) == ["A"]

    def test_a_pinned_camera_runs_without_a_deadline(self) -> None:
        r = rotation(slots=1, slice_seconds=10.0, pinned=("DEMO",))
        r.update_assignment(streams("A", "DEMO"))
        assert r.slice_for("DEMO") is None
        assert r.slice_for("A") == 10.0

    def test_over_pinning_is_reported(self, caplog) -> None:
        """Pinning more cameras than slots starves every rotating camera."""
        r = rotation(slots=1, pinned=("X", "Y"))
        with caplog.at_level("ERROR"):
            r.update_assignment(streams("X", "Y", "Z"))
        assert any("pinned" in record.message for record in caplog.records)


class TestCoverageIsStated:
    def test_a_fleet_that_fits_reports_no_sweep(self) -> None:
        r = rotation(slots=4, slice_seconds=30.0)
        r.update_assignment(streams("A", "B"))
        assert r.sweep_seconds == 0.0

    def test_sweep_time_is_the_honest_coverage_number(self) -> None:
        # 10 cameras, 2 slots, 30s each: each camera runs 30s in every 150s.
        r = rotation(slots=2, slice_seconds=30.0)
        r.update_assignment(streams(*[f"C{i}" for i in range(10)]))
        assert r.sweep_seconds == 150.0

    def test_a_pinned_camera_does_not_count_toward_the_sweep(self) -> None:
        # DEMO holds one of the two slots permanently, so nine cameras rotate
        # through the single remaining slot.
        r = rotation(slots=2, slice_seconds=30.0, pinned=("DEMO",))
        r.update_assignment(streams("DEMO", *[f"C{i}" for i in range(9)]))
        assert r.sweep_seconds == 270.0

    def test_no_rotation_means_no_sweep_claim(self) -> None:
        r = rotation(slots=1, slice_seconds=0.0)
        r.update_assignment(streams("A", "B", "C"))
        assert r.sweep_seconds == 0.0

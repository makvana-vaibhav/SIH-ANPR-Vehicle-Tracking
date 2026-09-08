"""Merging tracker fragments into physical vehicles.

One physical vehicle must produce one event, because the platform keys vehicle
history and cross-camera tracing on that. These tests check the merge is
aggressive enough to repair fragmentation and conservative enough not to fuse
two genuinely different vehicles.
"""

from __future__ import annotations

from ailab.config import RunConfig
from ailab.track.merge import fragmentation_stats, merge
from ailab.types import BBox, CropQuality, PlateConsensus, PlateRead, Track, TrackObservation


def make_track(
    track_id: int, plate: str, start_s: float, end_s: float,
    reads: int = 4, confidence: float = 0.95, grammar_valid: bool = True,
) -> Track:
    track = Track(track_id=track_id, class_name="car", class_id=2)
    frames = max(2, int((end_s - start_s) * 15))
    for i in range(frames):
        t = start_s + (end_s - start_s) * (i / max(1, frames - 1))
        track.observe(
            TrackObservation(
                frame_index=int(t * 15), t_s=t,
                bbox=BBox(100, 100, 300, 250), confidence=0.9, detected=True,
            )
        )
    for i in range(reads):
        track.plate_reads.append(
            PlateRead(
                text_raw=plate, text=plate, ocr_confidence=confidence,
                frame_index=track.first_frame + i, t_s=start_s, track_id=track_id,
                engine="test",
                quality=CropQuality(200.0, 128.0, 45.0, 140, 40),
                grammar_valid=grammar_valid,
            )
        )
    if plate:
        track.result = PlateConsensus(
            text=plate, confidence=confidence, method="char_vote",
            reads_total=reads, reads_agreeing=reads, grammar_valid=grammar_valid,
        )
    return track


def config() -> RunConfig:
    return RunConfig()


class TestMerge:
    def test_overlapping_fragments_of_one_plate_become_one_vehicle(self) -> None:
        """The measured failure: one car reported as three concurrent tracks."""
        tracks = {
            1: make_track(1, "GJ03AB1234", 0.0, 10.0),
            7: make_track(7, "GJ03AB1234", 3.0, 9.0),
            12: make_track(12, "GJ03AB1234", 6.0, 7.0),
        }
        vehicles = merge(tracks, config())
        assert len(vehicles) == 1
        assert vehicles[0].track_ids == [1, 7, 12]
        assert vehicles[0].merged_from_fragments == 3
        assert vehicles[0].plate == "GJ03AB1234"

    def test_pooled_reads_produce_a_better_supported_answer(self) -> None:
        tracks = {
            1: make_track(1, "GJ03AB1234", 0.0, 4.0, reads=3),
            2: make_track(2, "GJ03AB1234", 3.0, 7.0, reads=5),
        }
        vehicle = merge(tracks, config())[0]
        assert len(vehicle.reads) == 8
        assert vehicle.result is not None
        assert vehicle.result.reads_total == 8

    def test_one_dropped_character_still_merges(self) -> None:
        """Measured: GJ12HH8771 and GJ12H8771 were the same car."""
        tracks = {
            9: make_track(9, "GJ12HH8771", 5.0, 12.0, reads=12),
            24: make_track(24, "GJ12H8771", 11.0, 14.0, reads=3),
        }
        vehicles = merge(tracks, config())
        assert len(vehicles) == 1
        # Consensus over the pooled reads picks the better-supported spelling.
        assert vehicles[0].plate == "GJ12HH8771"

    def test_different_plates_are_not_merged(self) -> None:
        tracks = {
            1: make_track(1, "GJ03AB1234", 0.0, 5.0),
            2: make_track(2, "MH12XY9876", 1.0, 6.0),
        }
        assert len(merge(tracks, config())) == 2

    def test_the_same_plate_much_later_is_a_second_pass(self) -> None:
        """Two separate sightings must not become one long implausible one."""
        tracks = {
            1: make_track(1, "GJ03AB1234", 0.0, 5.0),
            2: make_track(2, "GJ03AB1234", 60.0, 65.0),
        }
        assert len(merge(tracks, config())) == 2

    def test_short_occlusion_gap_still_merges(self) -> None:
        tracks = {
            1: make_track(1, "GJ03AB1234", 0.0, 5.0),
            2: make_track(2, "GJ03AB1234", 6.0, 10.0),
        }
        assert len(merge(tracks, config())) == 1

    def test_plateless_tracks_stay_separate(self) -> None:
        """Two vehicles with no plate are not evidence of being one vehicle."""
        tracks = {
            1: make_track(1, "", 0.0, 5.0, reads=0),
            2: make_track(2, "", 1.0, 6.0, reads=0),
        }
        assert len(merge(tracks, config())) == 2

    def test_invalid_plates_are_not_merged_by_default(self) -> None:
        """A shared invalid string is a shared OCR failure, not a shared car."""
        tracks = {
            1: make_track(1, "XXXXXX", 0.0, 5.0, grammar_valid=False),
            2: make_track(2, "XXXXXX", 1.0, 6.0, grammar_valid=False),
        }
        assert len(merge(tracks, config())) == 2

    def test_merging_can_be_disabled(self) -> None:
        cfg = config()
        cfg.tracker.merge.enabled = False
        tracks = {
            1: make_track(1, "GJ03AB1234", 0.0, 10.0),
            2: make_track(2, "GJ03AB1234", 3.0, 9.0),
        }
        assert len(merge(tracks, cfg)) == 2

    def test_span_covers_all_fragments(self) -> None:
        tracks = {
            1: make_track(1, "GJ03AB1234", 2.0, 6.0),
            2: make_track(2, "GJ03AB1234", 5.0, 11.0),
        }
        vehicle = merge(tracks, config())[0]
        assert vehicle.first_seen_s == 2.0
        assert vehicle.last_seen_s == 11.0


def test_fragmentation_stats_report_the_repair() -> None:
    tracks = {
        1: make_track(1, "GJ03AB1234", 0.0, 10.0),
        7: make_track(7, "GJ03AB1234", 3.0, 9.0),
        3: make_track(3, "MH12XY9876", 1.0, 5.0),
    }
    vehicles = merge(tracks, config())
    stats = fragmentation_stats(tracks, vehicles)
    assert stats["raw_tracks"] == 3
    assert stats["merged_vehicles"] == 2
    assert stats["fragments_absorbed"] == 1
    assert stats["worst_fragmentation"] == 2


def test_invalid_fragment_joins_a_valid_cluster() -> None:
    """Measured: `JO8AJ1643` was `GJ08AJ1643` with the leading G dropped.

    The grammar rejects it, so it cannot start a cluster — but it can join one
    whose plate it nearly matches, and consensus then keeps the valid spelling.
    """
    tracks = {
        1: make_track(1, "GJ08AJ1643", 0.0, 6.0, reads=8),
        2: make_track(2, "JO8AJ1643", 5.0, 8.0, reads=2, grammar_valid=False),
    }
    vehicles = merge(tracks, config())
    assert len(vehicles) == 1
    assert vehicles[0].plate == "GJ08AJ1643"


def test_two_invalid_plates_do_not_form_a_vehicle() -> None:
    """A shared OCR failure is not evidence of a shared car."""
    tracks = {
        1: make_track(1, "ZZZZZZ", 0.0, 5.0, grammar_valid=False),
        2: make_track(2, "ZZZZZZ", 1.0, 6.0, grammar_valid=False),
    }
    assert len(merge(tracks, config())) == 2


def test_invalid_fragment_joins_even_when_it_started_first() -> None:
    """Order must not decide the outcome.

    The measured case had the corrupt fragment starting *earlier* than the good
    one, so a single ordered pass judged it before the cluster it belonged to
    existed and reported it as a second vehicle.
    """
    tracks = {
        1: make_track(1, "JO8AJ1643", 0.0, 3.0, reads=2, grammar_valid=False),
        2: make_track(2, "GJ08AJ1643", 2.0, 8.0, reads=9),
    }
    vehicles = merge(tracks, config())
    assert len(vehicles) == 1
    assert vehicles[0].plate == "GJ08AJ1643"

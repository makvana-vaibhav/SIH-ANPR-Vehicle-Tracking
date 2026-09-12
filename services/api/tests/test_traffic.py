"""The traffic metric formulas, pinned to worked examples.

`app.services.traffic` imports nothing but the standard library, which is what
lets this file run anywhere — including a machine with no Postgres, where the
rest of this suite cannot start. Every case below was executed while the code
was written, and two of them found real defects: occupancy double-counting a
handover, and the queue detector reporting the earlier of two equally-long
queues rather than the worse one.

Numbers here are *reasoned* defaults, not measurements from Ahmedabad traffic —
there is no calibrated ground truth for that in this repo. What these tests pin
is that the arithmetic does what the docstrings claim and that the boundaries
sit where `TrafficThresholds` says, so tuning the thresholds against a real
deployment changes behaviour visibly rather than silently.
"""

from __future__ import annotations

import pytest

from app.services.traffic import (
    QueueSample,
    TrafficThresholds,
    assess_congestion,
    classify_congestion,
    counts_by_type,
    detect_queue,
    is_possible_obstruction,
    median_or_none,
    occupancy_at,
    peak_occupancy,
    rate_per_hour,
    rate_per_minute,
    speed_ratio,
)


@pytest.fixture
def thresholds() -> TrafficThresholds:
    return TrafficThresholds()


class TestRates:
    def test_twelve_vehicles_in_six_minutes(self) -> None:
        assert rate_per_minute(12, 360) == 2.0
        assert rate_per_hour(12, 360) == 120.0

    def test_an_empty_window_has_no_rate_rather_than_a_zero_division(self) -> None:
        assert rate_per_minute(5, 0) is None
        assert rate_per_hour(5, 0) is None


class TestCountsByType:
    def test_every_known_class_is_present_even_at_zero(self) -> None:
        """A panel that drops 'bus' when none passed reads as broken."""
        counts = counts_by_type(["car", "car"])
        assert counts == {"car": 2, "motorcycle": 0, "bus": 0, "truck": 0, "other": 0}

    def test_case_is_normalised_and_strays_are_kept_as_other(self) -> None:
        counts = counts_by_type(["Bus", "TRUCK", "tractor", None])
        assert counts["bus"] == 1
        assert counts["truck"] == 1
        # Never discarded: an unexpected class is a fact about the detector.
        assert counts["other"] == 2


class TestOccupancy:
    """Density: how many vehicles were in view at once."""

    def test_three_overlapping_vehicles(self) -> None:
        assert peak_occupancy([(0, 20), (5, 25), (10, 30)]) == 3
        assert occupancy_at([(0, 20), (5, 25), (10, 30)], 12) == 3

    def test_a_handover_is_not_double_occupancy(self) -> None:
        """One vehicle leaving exactly as the next arrives is one vehicle.

        Counting the shared instant as two inflates every figure on a busy
        camera by one, which is worst precisely where the number matters.
        """
        assert peak_occupancy([(0, 10), (10, 20), (20, 30)]) == 1

    def test_nothing_in_view(self) -> None:
        assert peak_occupancy([]) == 0

    def test_reversed_intervals_are_tolerated(self) -> None:
        assert peak_occupancy([(20, 0)]) == 1


class TestCongestion:
    def test_the_scale_behaves_across_its_whole_range(
        self, thresholds: TrafficThresholds
    ) -> None:
        quiet = assess_congestion(
            peak_vehicles=2, speed_ratio=0.95, travel_time_delta_pct=0.0,
            thresholds=thresholds,
        )
        busy = assess_congestion(
            peak_vehicles=6, speed_ratio=0.70, travel_time_delta_pct=25.0,
            thresholds=thresholds,
        )
        heavy = assess_congestion(
            peak_vehicles=9, speed_ratio=0.45, travel_time_delta_pct=80.0,
            thresholds=thresholds,
        )
        gridlock = assess_congestion(
            peak_vehicles=12, speed_ratio=0.20, travel_time_delta_pct=180.0,
            thresholds=thresholds,
        )
        assert quiet.level == "free"
        assert busy.level == "moderate"
        assert heavy.level == "heavy"
        assert gridlock.level == "severe"
        # Monotonic, which is the property that makes "top congested roads"
        # a meaningful sort.
        scores = [quiet.score, busy.score, heavy.score, gridlock.score]
        assert scores == sorted(scores)

    def test_weights_are_renormalised_over_available_signals(
        self, thresholds: TrafficThresholds
    ) -> None:
        """A dead stop is fully congested even with no other signal.

        Scoring a missing signal as zero would weight this 0.35 and report
        moderate traffic on a road that is not moving — the demo fleet has no
        usable speed figure for most corridors, so this is the common case
        rather than an edge one.
        """
        stopped = assess_congestion(
            peak_vehicles=None, speed_ratio=0.0, travel_time_delta_pct=None,
            thresholds=thresholds,
        )
        assert stopped.score == 1.0
        assert stopped.level == "severe"

    def test_speed_and_travel_time_without_occupancy(
        self, thresholds: TrafficThresholds
    ) -> None:
        assessment = assess_congestion(
            peak_vehicles=None, speed_ratio=0.35, travel_time_delta_pct=140.0,
            thresholds=thresholds,
        )
        assert assessment.level == "severe"
        assert assessment.status == "ok"

    def test_no_signal_at_all_refuses_rather_than_guessing(
        self, thresholds: TrafficThresholds
    ) -> None:
        assessment = assess_congestion(
            peak_vehicles=None, speed_ratio=None, travel_time_delta_pct=None,
            thresholds=thresholds,
        )
        assert assessment.level is None
        assert assessment.score is None
        assert assessment.status == "insufficient_data"
        assert [f.factor for f in assessment.factors] == ["no_signal"]

    def test_every_level_carries_its_reasons(
        self, thresholds: TrafficThresholds
    ) -> None:
        """CLAUDE.md's explainability rule: a score with no reasons is a bug."""
        assessment = assess_congestion(
            peak_vehicles=9, speed_ratio=0.45, travel_time_delta_pct=80.0,
            thresholds=thresholds,
        )
        assert {f.factor for f in assessment.factors} == {
            "occupancy",
            "speed",
            "travel_time",
        }
        assert all(f.detail for f in assessment.factors)

    @pytest.mark.parametrize(
        ("score", "level"),
        [
            (0.0, "free"),
            (0.299, "free"),
            (0.30, "moderate"),
            (0.549, "moderate"),
            (0.55, "heavy"),
            (0.749, "heavy"),
            (0.75, "severe"),
            (1.0, "severe"),
        ],
    )
    def test_thresholds_are_exactly_where_they_claim_to_be(
        self, score: float, level: str, thresholds: TrafficThresholds
    ) -> None:
        assert classify_congestion(score, thresholds) == level

    def test_an_extreme_reading_cannot_escape_the_scale(
        self, thresholds: TrafficThresholds
    ) -> None:
        """Clamped, so one absurd input cannot dominate the others."""
        assessment = assess_congestion(
            peak_vehicles=9_999, speed_ratio=-5.0, travel_time_delta_pct=99_999.0,
            thresholds=thresholds,
        )
        assert assessment.score == 1.0


class TestQueues:
    def _samples(self, spec: list[tuple[int, float]]) -> list[QueueSample]:
        return [
            QueueSample(at=float(i), stationary_vehicles=n, median_dwell_s=d)
            for i, (n, d) in enumerate(spec)
        ]

    def test_a_sustained_queue_is_detected(self, thresholds: TrafficThresholds) -> None:
        queue = detect_queue(self._samples([(5, 40), (6, 45), (7, 50)]), thresholds)
        assert queue.present
        assert queue.length_vehicles == 7
        assert queue.sustained_buckets == 3

    def test_one_bucket_is_not_sustained(self, thresholds: TrafficThresholds) -> None:
        """Otherwise every red light reports a queue."""
        queue = detect_queue(self._samples([(6, 45), (1, 5)]), thresholds)
        assert not queue.present

    def test_enough_vehicles_but_none_of_them_waiting(
        self, thresholds: TrafficThresholds
    ) -> None:
        """Eight vehicles flowing through is traffic, not a queue."""
        queue = detect_queue(self._samples([(8, 10), (9, 12)]), thresholds)
        assert not queue.present

    def test_a_long_wait_by_too_few_vehicles(
        self, thresholds: TrafficThresholds
    ) -> None:
        queue = detect_queue(self._samples([(2, 90), (2, 95)]), thresholds)
        assert not queue.present

    def test_the_worst_queue_is_reported_whichever_came_first(
        self, thresholds: TrafficThresholds
    ) -> None:
        """Two equally-long queues in one window: report the bigger.

        This was a real defect — taking the length from whichever run was
        longest kept the earlier, smaller queue when the runs tied, and the
        panel under-reported.
        """
        later = detect_queue(
            self._samples([(5, 30), (6, 35), (0, 0), (9, 40), (9, 45)]), thresholds
        )
        earlier = detect_queue(
            self._samples([(9, 40), (9, 45), (0, 0), (5, 30), (6, 35)]), thresholds
        )
        assert later.length_vehicles == 9
        assert earlier.length_vehicles == 9

    def test_no_samples_is_not_an_empty_road(
        self, thresholds: TrafficThresholds
    ) -> None:
        queue = detect_queue([], thresholds)
        assert not queue.present
        assert queue.status == "insufficient_data"


class TestPossibleObstruction:
    def test_a_vehicle_stopped_for_two_minutes(
        self, thresholds: TrafficThresholds
    ) -> None:
        assert is_possible_obstruction(
            direction="stationary", dwell_s=120.0, thresholds=thresholds
        )

    def test_a_vehicle_queueing_briefly_is_not_an_obstruction(
        self, thresholds: TrafficThresholds
    ) -> None:
        assert not is_possible_obstruction(
            direction="stationary", dwell_s=30.0, thresholds=thresholds
        )

    def test_a_slow_but_moving_vehicle_is_not_an_obstruction(
        self, thresholds: TrafficThresholds
    ) -> None:
        assert not is_possible_obstruction(
            direction="approaching", dwell_s=200.0, thresholds=thresholds
        )

    def test_unmeasurable_motion_is_never_an_obstruction(
        self, thresholds: TrafficThresholds
    ) -> None:
        """A one-sighting detector flicker stores NULL, not 'stationary'.

        Treating "we could not measure" as "it did not move" would invent
        stopped vehicles out of tracker noise, on a panel whose whole value is
        that an operator can trust it enough to look.
        """
        assert not is_possible_obstruction(
            direction=None, dwell_s=300.0, thresholds=thresholds
        )
        assert not is_possible_obstruction(
            direction="stationary", dwell_s=None, thresholds=thresholds
        )


class TestSpeedHelpers:
    def test_ratio_against_free_flow(self) -> None:
        assert speed_ratio(30, 60) == 0.5

    def test_unknown_or_impossible_reference_yields_nothing(self) -> None:
        assert speed_ratio(30, 0) is None
        assert speed_ratio(30, None) is None
        assert speed_ratio(None, 60) is None

    def test_median_ignores_absent_values(self) -> None:
        assert median_or_none([10.0, 20.0, 30.0]) == 20.0
        assert median_or_none([]) is None

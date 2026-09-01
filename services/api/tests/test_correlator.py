"""Route reconstruction: the journey, and what the system refuses to claim.

Two failures matter here and they pull in opposite directions. Dropping a leg
the system cannot explain produces a clean route that hides the interesting
part — a cloned plate looks exactly like an impossible hop. Keeping every leg
without marking it produces a route that asserts more than the evidence
supports. These tests pin the middle: nothing is removed, everything doubtful
is labelled.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app.services.correlator import (
    MAX_PLAUSIBLE_KMPH,
    Hop,
    Route,
    Sighting,
    angular_gap,
    bearing_deg,
    cluster_into_hops,
    haversine_m,
    score_legs,
)

# Real Gujarat coordinates, so distances are checkable against a map.
RAJKOT = (22.3039, 70.8022)
GONDAL = (21.9611, 70.8028)
JETPUR = (21.7549, 70.6205)
JUNAGADH = (21.5222, 70.4579)

T0 = datetime(2026, 9, 1, 8, 0, 0, tzinfo=UTC)


def sighting(
    place: tuple[float, float],
    code: str,
    at: datetime,
    *,
    confidence: float = 0.95,
    heading: float | None = None,
    camera_id: uuid.UUID | None = None,
) -> Sighting:
    return Sighting(
        detection_id=uuid.uuid4(),
        ts=at,
        camera_id=camera_id or uuid.uuid5(uuid.NAMESPACE_DNS, code),
        camera_code=code,
        camera_name=f"{code} camera",
        city=code.title(),
        district=code.title(),
        lat=place[0],
        lon=place[1],
        heading_deg=heading,
        plate_confidence=confidence,
    )


def route_of(*sightings: Sighting, headings: dict | None = None) -> Route:
    hops = cluster_into_hops(list(sightings))
    score_legs(hops, headings or {})
    return Route(plate="GJ03AB1234", hops=hops, window_from=None, window_to=None)


class TestGeometry:
    def test_distance_matches_the_map(self) -> None:
        """Rajkot to Junagadh is about 93 km as the crow flies."""
        metres = haversine_m(*RAJKOT, *JUNAGADH)
        assert 90_000 < metres < 96_000

    def test_distance_is_zero_for_the_same_point(self) -> None:
        assert haversine_m(*RAJKOT, *RAJKOT) == 0.0

    def test_bearing_south_is_about_180(self) -> None:
        # Gondal is almost due south of Rajkot.
        assert 170 < bearing_deg(*RAJKOT, *GONDAL) < 190

    def test_angular_gap_wraps_around_north(self) -> None:
        assert angular_gap(350.0, 10.0) == 20.0
        assert angular_gap(10.0, 350.0) == 20.0

    def test_angular_gap_never_exceeds_a_half_turn(self) -> None:
        for a in range(0, 360, 17):
            for b in range(0, 360, 23):
                assert 0.0 <= angular_gap(float(a), float(b)) <= 180.0


class TestClustering:
    def test_a_queue_at_one_junction_is_one_hop(self) -> None:
        """Forty detections while waiting at a signal is one visit, not forty."""
        sightings = [sighting(RAJKOT, "RJT", T0 + timedelta(seconds=5 * i)) for i in range(40)]
        hops = cluster_into_hops(sightings)
        assert len(hops) == 1
        assert hops[0].sightings == 40
        assert hops[0].dwell_s == 195.0

    def test_a_return_visit_is_a_separate_hop(self) -> None:
        """A → B → A has genuinely visited A twice; merging erases the return."""
        hops = cluster_into_hops(
            [
                sighting(RAJKOT, "RJT", T0),
                sighting(GONDAL, "GND", T0 + timedelta(minutes=30)),
                sighting(RAJKOT, "RJT", T0 + timedelta(minutes=60)),
            ]
        )
        assert [h.camera_code for h in hops] == ["RJT", "GND", "RJT"]

    def test_a_long_gap_at_one_camera_splits_the_hop(self) -> None:
        hops = cluster_into_hops(
            [
                sighting(RAJKOT, "RJT", T0),
                sighting(RAJKOT, "RJT", T0 + timedelta(hours=3)),
            ]
        )
        assert len(hops) == 2

    def test_sightings_are_ordered_by_time_not_by_arrival(self) -> None:
        hops = cluster_into_hops(
            [
                sighting(JUNAGADH, "JND", T0 + timedelta(hours=3)),
                sighting(RAJKOT, "RJT", T0),
                sighting(GONDAL, "GND", T0 + timedelta(hours=1)),
            ]
        )
        assert [h.camera_code for h in hops] == ["RJT", "GND", "JND"]

    def test_the_best_read_in_a_cluster_is_kept(self) -> None:
        hops = cluster_into_hops(
            [
                sighting(RAJKOT, "RJT", T0, confidence=0.55),
                sighting(RAJKOT, "RJT", T0 + timedelta(seconds=10), confidence=0.97),
            ]
        )
        assert hops[0].best_confidence == 0.97


class TestLegScoring:
    def test_a_normal_drive_is_not_flagged(self) -> None:
        # Rajkot → Gondal, ~38 km, in an hour: about 38 km/h. Ordinary.
        route = route_of(
            sighting(RAJKOT, "RJT", T0),
            sighting(GONDAL, "GND", T0 + timedelta(hours=1)),
        )
        assert route.hops[1].flags == []
        assert 30 < route.hops[1].implied_kmph < 45
        assert route.is_plausible

    def test_an_impossible_speed_is_flagged(self) -> None:
        """93 km in four minutes is 1,400 km/h. A car did not do that."""
        route = route_of(
            sighting(RAJKOT, "RJT", T0),
            sighting(JUNAGADH, "JND", T0 + timedelta(minutes=4)),
        )
        assert "implausible_speed" in route.hops[1].flags
        assert route.hops[1].implied_kmph > MAX_PLAUSIBLE_KMPH
        assert not route.is_plausible

    def test_the_same_plate_in_two_places_at_once_is_flagged(self) -> None:
        """The cloned-plate signature, and the most useful finding here."""
        route = route_of(
            sighting(RAJKOT, "RJT", T0),
            sighting(JUNAGADH, "JND", T0),
        )
        assert "impossible_simultaneous" in route.hops[1].flags
        assert "implausible_speed" in route.hops[1].flags
        assert not route.is_plausible

    def test_two_cameras_on_one_gantry_get_no_speed(self) -> None:
        """A metre of position error over four seconds is 900 km/h."""
        nearby = (RAJKOT[0] + 0.0001, RAJKOT[1])
        route = route_of(
            sighting(RAJKOT, "RJT-A", T0),
            sighting(nearby, "RJT-B", T0 + timedelta(seconds=4)),
        )
        assert route.hops[1].flags == ["co_located"]
        assert route.hops[1].implied_kmph is None
        assert route.is_plausible, "co-location is not an implausibility"

    def test_a_long_unwatched_gap_is_marked_but_not_disbelieved(self) -> None:
        """Most of Gujarat has no camera. That bounds the claim, not the truth."""
        route = route_of(
            sighting(RAJKOT, "RJT", T0),
            sighting(GONDAL, "GND", T0 + timedelta(hours=5)),
        )
        assert "unobserved_gap" in route.hops[1].flags
        assert route.is_plausible

    def test_speed_is_measured_from_departure_not_arrival(self) -> None:
        """Charging a vehicle for time it spent parked understates its speed."""
        parked = route_of(
            sighting(RAJKOT, "RJT", T0),
            sighting(RAJKOT, "RJT", T0 + timedelta(minutes=4)),
            sighting(GONDAL, "GND", T0 + timedelta(minutes=34)),
        )
        # Left Rajkot at T0+4min, arrived Gondal at T0+34min: 30 minutes.
        assert parked.hops[1].elapsed_s == 1800.0
        assert 70 < parked.hops[1].implied_kmph < 82

    def test_a_camera_facing_away_flags_the_leg(self) -> None:
        camera = uuid.uuid5(uuid.NAMESPACE_DNS, "RJT")
        route = route_of(
            sighting(RAJKOT, "RJT", T0, heading=0.0, camera_id=camera),
            sighting(GONDAL, "GND", T0 + timedelta(hours=1)),
            headings={camera: 0.0},  # facing north; travel is south
        )
        assert "heading_conflict" in route.hops[1].flags

    def test_a_camera_facing_the_right_way_does_not(self) -> None:
        camera = uuid.uuid5(uuid.NAMESPACE_DNS, "RJT")
        route = route_of(
            sighting(RAJKOT, "RJT", T0, heading=180.0, camera_id=camera),
            sighting(GONDAL, "GND", T0 + timedelta(hours=1)),
            headings={camera: 180.0},
        )
        assert "heading_conflict" not in route.hops[1].flags

    def test_the_first_hop_has_no_leg(self) -> None:
        route = route_of(sighting(RAJKOT, "RJT", T0))
        assert route.hops[0].distance_m is None
        assert route.hops[0].implied_kmph is None
        assert route.hops[0].flags == []


class TestNothingIsDropped:
    def test_an_impossible_leg_stays_in_the_route(self) -> None:
        """A route that deletes its inconvenient parts cannot be audited."""
        route = route_of(
            sighting(RAJKOT, "RJT", T0),
            sighting(JUNAGADH, "JND", T0 + timedelta(minutes=2)),
            sighting(GONDAL, "GND", T0 + timedelta(hours=3)),
        )
        assert len(route.hops) == 3
        assert len(route.flagged_hops) >= 1
        assert not route.is_plausible

    def test_confidence_falls_with_each_impossible_leg(self) -> None:
        clean = route_of(
            sighting(RAJKOT, "RJT", T0, confidence=0.9),
            sighting(GONDAL, "GND", T0 + timedelta(hours=1), confidence=0.9),
        )
        dirty = route_of(
            sighting(RAJKOT, "RJT", T0, confidence=0.9),
            sighting(JUNAGADH, "JND", T0 + timedelta(minutes=2), confidence=0.9),
        )
        assert dirty.confidence < clean.confidence

    def test_confidence_is_bounded_by_the_weakest_read(self) -> None:
        """One shaky read can attach another vehicle's journey to this one."""
        route = route_of(
            sighting(RAJKOT, "RJT", T0, confidence=0.99),
            sighting(GONDAL, "GND", T0 + timedelta(hours=1), confidence=0.42),
        )
        assert route.confidence == 0.42


class TestTheFullJourney:
    def _four_hop(self) -> Route:
        return route_of(
            sighting(RAJKOT, "RJT", T0),
            sighting(GONDAL, "GND", T0 + timedelta(hours=1)),
            sighting(JETPUR, "JTP", T0 + timedelta(hours=2)),
            sighting(JUNAGADH, "JND", T0 + timedelta(hours=3)),
        )

    def test_the_route_down_the_corridor_reconstructs(self) -> None:
        route = self._four_hop()
        assert [h.camera_code for h in route.hops] == ["RJT", "GND", "JTP", "JND"]
        assert route.camera_count == 4
        assert route.is_plausible
        assert route.flagged_hops == []

    def test_every_implied_speed_is_a_road_speed(self) -> None:
        for hop in self._four_hop().hops[1:]:
            assert 10 < (hop.implied_kmph or 0) < MAX_PLAUSIBLE_KMPH

    def test_the_total_is_a_lower_bound_on_distance_driven(self) -> None:
        # Straight-line Rajkot→Junagadh is ~93 km; the leg sum is longer
        # because the corridor bends, and both under-state the road distance.
        route = self._four_hop()
        assert route.distance_m >= haversine_m(*RAJKOT, *JUNAGADH)

    def test_duration_spans_first_to_last(self) -> None:
        assert self._four_hop().duration_s == 3 * 3600.0


class TestGeoJson:
    def test_the_shape_is_valid_geojson(self) -> None:
        route = route_of(
            sighting(RAJKOT, "RJT", T0),
            sighting(GONDAL, "GND", T0 + timedelta(hours=1)),
        )
        payload = route.to_geojson()
        assert payload["type"] == "FeatureCollection"
        assert all(
            f["type"] == "Feature" and "geometry" in f and "properties" in f
            for f in payload["features"]
        )

    def test_coordinates_are_longitude_first(self) -> None:
        """GeoJSON is [lon, lat]; getting it backwards puts Gujarat in the sea."""
        route = route_of(sighting(RAJKOT, "RJT", T0))
        point = route.to_geojson()["features"][0]["geometry"]["coordinates"]
        assert point == [round(RAJKOT[1], 6), round(RAJKOT[0], 6)]
        assert 68 < point[0] < 75, "longitude should be Gujarat's, not its latitude"

    def test_the_line_declares_that_it_is_not_the_road(self) -> None:
        route = route_of(
            sighting(RAJKOT, "RJT", T0),
            sighting(GONDAL, "GND", T0 + timedelta(hours=1)),
        )
        line = next(
            f for f in route.to_geojson()["features"] if f["geometry"]["type"] == "LineString"
        )
        assert "not the roads driven" in line["properties"]["geometry_note"]

    def test_a_single_sighting_draws_a_point_and_no_line(self) -> None:
        route = route_of(sighting(RAJKOT, "RJT", T0))
        kinds = [f["geometry"]["type"] for f in route.to_geojson()["features"]]
        assert kinds == ["Point"]

    def test_hops_carry_their_sequence(self) -> None:
        route = route_of(
            sighting(RAJKOT, "RJT", T0),
            sighting(GONDAL, "GND", T0 + timedelta(hours=1)),
        )
        points = [f for f in route.to_geojson()["features"] if f["properties"]["kind"] == "hop"]
        assert [p["properties"]["sequence"] for p in points] == [0, 1]


class TestEmptyRoutes:
    def test_a_plate_never_seen_has_an_empty_route(self) -> None:
        route = Route(plate="GJ99ZZ9999", hops=[], window_from=None, window_to=None)
        assert route.hops == []
        assert route.camera_count == 0
        assert route.confidence == 0.0
        assert route.distance_m == 0.0
        assert route.duration_s == 0.0
        assert route.is_plausible, "no evidence is not the same as bad evidence"

    def test_an_empty_route_still_serialises(self) -> None:
        route = Route(plate="GJ99ZZ9999", hops=[], window_from=None, window_to=None)
        assert route.to_geojson()["features"] == []
        assert route.to_dict()["hop_count"] == 0


class TestHopSerialisation:
    def test_a_hop_reports_kilometres_as_well_as_metres(self) -> None:
        hop = Hop(
            camera_id=uuid.uuid4(),
            camera_code="RJT",
            camera_name="Rajkot",
            city="Rajkot",
            district="Rajkot",
            lat=RAJKOT[0],
            lon=RAJKOT[1],
            arrived_at=T0,
            departed_at=T0,
            sightings=1,
            best_confidence=0.9,
            distance_m=1500.0,
        )
        assert hop.to_dict()["distance_km"] == 1.5

    def test_a_first_hop_serialises_its_missing_leg_as_null(self) -> None:
        route = route_of(sighting(RAJKOT, "RJT", T0))
        first = route.hops[0].to_dict()
        assert first["distance_km"] is None
        assert first["implied_kmph"] is None
        assert first["plausible"] is True

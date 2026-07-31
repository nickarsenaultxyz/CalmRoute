"""Conflation geometry helpers.

The bearing functions carry the weight here: they are what stops a shared-use
path from claiming the arterial it crosses, and the doubled-angle averaging is
easy to get subtly wrong at the 0/180 wrap point.
"""

from __future__ import annotations

import math

import geopandas as gpd
import pytest
from shapely import STRtree
from shapely.geometry import LineString, MultiLineString

from lexbike import params as params_mod
from lexbike.conflate import (
    _dedupe_connectors,
    _find_attachments,
    _find_path_junctions,
    bearing_delta,
    conflate_on_road,
    line_bearing,
    local_bearing_delta,
)


def test_bearing_of_cardinal_directions():
    assert line_bearing(LineString([(0, 0), (0, 10)])) == pytest.approx(0.0)     # north
    assert line_bearing(LineString([(0, 0), (10, 0)])) == pytest.approx(90.0)    # east
    assert line_bearing(LineString([(0, 0), (10, 10)])) == pytest.approx(45.0)   # north-east


def test_bearing_is_undirected():
    """A line and its reverse describe the same alignment."""
    fwd = line_bearing(LineString([(0, 0), (3, 7)]))
    rev = line_bearing(LineString([(3, 7), (0, 0)]))
    assert fwd == pytest.approx(rev)


def test_bearing_averages_in_doubled_angle_space():
    """A near-north line whose vertices straddle the 0/180 wrap must average to
    ~north, not to ~90 degrees.

    This is the failure a plain circular mean of undirected bearings produces,
    and it would reject a facility running parallel to its own street.
    """
    # alternating slightly east-of-north and slightly west-of-north
    line = LineString([(0, 0), (0.2, 10), (-0.2, 20), (0.2, 30), (-0.2, 40)])
    b = line_bearing(line)
    assert min(b, 180 - b) < 3.0, f"expected ~north, got {b}"


def test_bearing_is_length_weighted_not_vertex_counted():
    """A long north leg with a few short jitters stays north.

    Endpoint-to-endpoint bearing (the old implementation) is meaningless for a
    curved segment; an unweighted vertex mean is skewed by dense short jitters.
    """
    coords = [(0, 0), (0, 100)] + [(0.5, 100.5), (0, 101), (0.5, 101.5)]
    b = line_bearing(LineString(coords))
    assert min(b, 180 - b) < 10.0


def test_bearing_of_a_curve_reflects_its_overall_run():
    """A quarter arc from (0,10) to (10,0) runs south-east: its chord bearing is
    135 degrees, and the length-weighted mean tangent should agree with it."""
    pts = [(10 * math.sin(t), 10 * math.cos(t))
           for t in [i * (math.pi / 2) / 24 for i in range(25)]]
    arc = LineString(pts)
    chord = line_bearing(LineString([pts[0], pts[-1]]))
    assert chord == pytest.approx(135.0, abs=0.5)
    assert line_bearing(arc) == pytest.approx(135.0, abs=2.0)


def test_bearing_handles_multilinestring():
    mls = MultiLineString([[(0, 0), (0, 10)], [(0, 20), (0, 30)]])
    assert line_bearing(mls) == pytest.approx(0.0)


def test_bearing_of_degenerate_geometry_is_none():
    """Returns None explicitly rather than swallowing an exception, which is what
    the old bare `except:` did -- it also caught KeyboardInterrupt."""
    assert line_bearing(LineString([(5, 5), (5, 5)])) is None


def test_bearing_delta_wraps_correctly():
    assert bearing_delta(10.0, 20.0) == pytest.approx(10.0)
    assert bearing_delta(175.0, 5.0) == pytest.approx(10.0), "must wrap across 180"
    assert bearing_delta(0.0, 90.0) == pytest.approx(90.0)
    assert bearing_delta(0.0, 179.0) == pytest.approx(1.0)
    assert bearing_delta(45.0, 135.0) == pytest.approx(90.0)


def test_bearing_delta_is_symmetric_and_bounded():
    for a in range(0, 180, 7):
        for b in range(0, 180, 11):
            d = bearing_delta(float(a), float(b))
            assert 0.0 <= d <= 90.0
            assert d == pytest.approx(bearing_delta(float(b), float(a)))


def test_bearing_delta_with_unknown_is_none():
    assert bearing_delta(None, 10.0) is None
    assert bearing_delta(10.0, None) is None


def test_perpendicular_crossing_is_rejected_by_the_tolerance():
    """The case the directional check exists for: a trail crossing an arterial
    must not be credited to it."""
    trail = line_bearing(LineString([(0, 0), (100, 0)]))      # east-west
    arterial = line_bearing(LineString([(50, -50), (50, 50)]))  # north-south
    assert bearing_delta(trail, arterial) == pytest.approx(90.0)
    assert bearing_delta(trail, arterial) > 30.0  # the configured tolerance


def test_local_bearing_uses_the_part_of_a_long_curve_beside_the_street():
    """A long U-shaped facility must not be judged by its whole-record bearing.

    This is the geometry behind the alternating ratings on Beaumont Centre
    Circle: one continuous facility record bends around many short centreline
    blocks. The old global comparison saw the horizontal block as perpendicular
    to the mostly vertical facility and dropped it.
    """
    facility = LineString([(0, 0), (0, 100), (100, 100), (100, 0)])
    street = LineString([(20, 104), (80, 104)])
    buffer_m = 8

    global_delta = bearing_delta(line_bearing(street), line_bearing(facility))
    local_delta = local_bearing_delta(
        street,
        facility,
        street.buffer(buffer_m),
        facility.buffer(buffer_m),
    )

    assert global_delta > 30
    assert local_delta == pytest.approx(0.0)


def test_conflation_credits_the_nearby_part_of_a_long_curve():
    """Exercise the full assignment path, not only the geometry helper."""
    streets = gpd.GeoDataFrame(
        {"id": [1], "rdclass": [5]},
        geometry=[LineString([(20, 104), (80, 104)])],
        crs=32616,
    )
    facilities = gpd.GeoDataFrame(
        {
            "id_src": [207],
            "on_road": [True],
            "fac": ["buffered"],
        },
        geometry=[LineString([(0, 0), (0, 100), (100, 100), (100, 0)])],
        crs=32616,
    )

    out = conflate_on_road(
        streets,
        facilities,
        params_mod.load(),
        check_quality=False,
    )

    assert out.loc[0, "fac"] == "buffered"
    assert out.loc[0, "fac_source_ids"] == [207]


def test_named_corridor_reaches_both_carriageways_but_not_another_road():
    """One corridor facility line can represent two parallel one-way streets.

    The extra reach is safe only with exact road-name agreement: an unrelated
    parallel road at the same distance must remain untreated.
    """
    streets = gpd.GeoDataFrame(
        {
            "id": [1, 2],
            "rdclass": [5, 5],
            "road_name": ["Beaumont Centre Cir", "Nearby Service Rd"],
        },
        geometry=[
            LineString([(0, 15), (100, 15)]),
            LineString([(0, -15), (100, -15)]),
        ],
        crs=32616,
    )
    facilities = gpd.GeoDataFrame(
        {
            "id_src": [207],
            "on_road": [True],
            "fac": ["buffered"],
            "network_name": ["BEAUMONT CENTRE CIR"],
        },
        geometry=[LineString([(0, 0), (100, 0)])],
        crs=32616,
    )

    out = conflate_on_road(
        streets,
        facilities,
        params_mod.load(),
        check_quality=False,
    )

    assert out["fac"].tolist() == ["buffered", "none"]
    assert out["fac_source_ids"].tolist() == [[207], []]


def test_local_bearing_still_rejects_a_perpendicular_crossing():
    street = LineString([(-20, 0), (20, 0)])
    facility = LineString([(0, -20), (0, 20)])
    buffer_m = 8

    delta = local_bearing_delta(
        street,
        facility,
        street.buffer(buffer_m),
        facility.buffer(buffer_m),
    )

    assert delta == pytest.approx(90.0)
    assert delta > 30


def test_nearby_distinct_junctions_are_not_thinned_by_along_path_distance():
    """Two streets near opposite ends of a short path are two real exits.

    The old 120 m thinning window kept the first and silently dropped the
    second. At Baptist Health that removed the 9 m connection to Hiltonia Park
    and sent routes on a large detour.
    """
    path = LineString([(0, 0), (0, 100)])
    targets = gpd.GeoDataFrame(
        geometry=[
            LineString([(-10, -1), (10, -1)]),
            LineString([(-10, -2), (10, -2)]),
            LineString([(-10, 101), (10, 101)]),
        ],
        crs=32616,
    )

    found = _find_attachments(
        path,
        targets,
        STRtree(targets.geometry.values),
        max_m=25,
        spacing_m=120,
        merge_m=0.75,
    )

    assert len(found) == 2
    assert [round(item[0]) for item in found] == [0, 100]


def test_campus_endpoint_only_attachment_ignores_parallel_street_blocks():
    """A campus sidewalk joins streets at entrances, not once per block."""
    path = LineString([(0, 0), (0, 50), (0, 100)])
    targets = gpd.GeoDataFrame(
        geometry=[
            LineString([(-5, 0), (5, 0)]),
            LineString([(-5, 50), (5, 50)]),
            LineString([(-5, 100), (5, 100)]),
        ],
        crs=32616,
    )
    found = _find_attachments(
        path,
        targets,
        STRtree(targets.geometry.values),
        max_m=25,
        spacing_m=120,
        merge_m=0.75,
        endpoints_only=True,
    )
    assert [round(item[0]) for item in found] == [0, 100]


def test_path_junctions_use_tight_radius_away_from_endpoints():
    path = LineString([(0, 0), (0, 50), (0, 100)])
    others = gpd.GeoDataFrame(
        geometry=[
            # A real endpoint landing mismatch.
            LineString([(7, -10), (7, 10)]),
            # A merely parallel path near the interior vertex.
            LineString([(7, 40), (7, 60)]),
        ],
        crs=32616,
    )
    off = gpd.GeoDataFrame(
        geometry=[path, *others.geometry],
        crs=32616,
    )
    found = _find_path_junctions(
        path,
        0,
        off,
        STRtree(off.geometry.values),
        endpoint_max_m=8,
        interior_max_m=0.75,
    )
    targets = {item[2] for item in found}
    assert -2 in targets
    assert -3 not in targets


def test_identical_connectors_are_emitted_once():
    geometry = LineString([(0, 0), (5, 0)])
    rows = [
        {"geometry": geometry, "path_row": 1},
        {"geometry": LineString(reversed(geometry.coords)), "path_row": 2},
    ]
    assert len(_dedupe_connectors(rows)) == 1


def test_street_attachment_near_endpoint_snaps_to_endpoint():
    path = LineString([(0, 0), (0, 10)])
    target = LineString([(0.5, 0.2), (10, 0.2)])
    targets = gpd.GeoDataFrame(geometry=[target], crs=32616)
    found = _find_attachments(
        path,
        targets,
        STRtree(targets.geometry.values),
        max_m=25,
        spacing_m=120,
        merge_m=0.75,
    )
    assert tuple(found[0][1].coords[0]) == tuple(target.coords[0])

"""Conflation geometry helpers.

The bearing functions carry the weight here: they are what stops a shared-use
path from claiming the arterial it crosses, and the doubled-angle averaging is
easy to get subtly wrong at the 0/180 wrap point.
"""

from __future__ import annotations

import math

import pytest
from shapely.geometry import LineString, MultiLineString

from lexbike.conflate import bearing_delta, line_bearing


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

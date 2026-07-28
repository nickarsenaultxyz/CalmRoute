"""Graph construction, splitting, island labelling and barrier ranking.

Uses small synthetic networks so each behaviour is isolated. The real-data
figures are pinned by the aggregate-stability test instead.
"""

from __future__ import annotations

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString, MultiLineString

from lexbike import network as net
from lexbike import params as params_mod


@pytest.fixture(scope="module")
def params():
    return params_mod.load()


# Synthetic geometry must sit inside UTM zone 16N or reprojection yields NaN
# lengths, so everything is offset from downtown Lexington.
LON0, LAT0 = -84.50, 38.04


def at(dx: float, dy: float = 0.0) -> tuple[float, float]:
    """A point ``dx`` degrees east and ``dy`` north of the Lexington origin."""
    return (LON0 + dx, LAT0 + dy)


def frame(rows, crs="EPSG:4326"):
    """Build a minimal network frame in WGS84."""
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=crs)


# ---------------------------------------------------------------------------
#  Cutting
# ---------------------------------------------------------------------------

def test_cut_splits_at_one_point():
    line = LineString([(0, 0), (10, 0)])
    pieces = net._cut(line, [4.0])
    assert len(pieces) == 2
    assert pieces[0].length == pytest.approx(4.0)
    assert pieces[1].length == pytest.approx(6.0)


def test_cut_preserves_total_length():
    line = LineString([(0, 0), (5, 0), (5, 5), (10, 5)])
    pieces = net._cut(line, [2.0, 7.0, 11.0])
    assert len(pieces) == 4
    assert sum(p.length for p in pieces) == pytest.approx(line.length)


def test_cut_pieces_join_end_to_end():
    """Adjacent pieces must share an exact coordinate, or splitting would create
    the very disconnection it exists to prevent."""
    line = LineString([(0, 0), (10, 0), (10, 10)])
    pieces = net._cut(line, [3.0, 14.0])
    for a, b in zip(pieces, pieces[1:]):
        assert a.coords[-1] == b.coords[0]


def test_cut_ignores_degenerate_distances():
    line = LineString([(0, 0), (10, 0)])
    assert len(net._cut(line, [])) == 1


# ---------------------------------------------------------------------------
#  Endpoints / node identity
# ---------------------------------------------------------------------------

def test_endpoints_round_to_requested_precision():
    line = LineString([(0.1234567, 1.0), (2.0, 3.7654321)])
    a, b = net._endpoints(line, 5)
    assert a == (0.12346, 1.0)
    assert b == (2.0, 3.76543)


def test_endpoints_rejects_multipart():
    """The old get_endpoints concatenated all parts and took first/last,
    inventing a pair that corresponded to no actual line."""
    mls = MultiLineString([[(0, 0), (1, 1)], [(5, 5), (6, 6)]])
    assert net._endpoints(mls, 5) is None


def test_endpoints_rejects_single_vertex():
    assert net._endpoints(LineString([(0, 0), (0, 0)]), 5) is not None  # 2 coords
    assert net._endpoints(LineString(), 5) is None


def test_shared_endpoints_become_one_node(params):
    """Two segments meeting at an identical coordinate must connect.

    This is what the old snap_to_grid broke: rounding each coordinate to a
    multiple of the tolerance put endpoints a fraction of a metre apart into
    different cells.
    """
    edges = frame([
        {"id": 1, "lts": 2, "geometry": LineString([at(0), at(0.001)])},
        {"id": 2, "lts": 2, "geometry": LineString([at(0.001), at(0.002)])},
    ])
    graph, node_ids, pairs = net.build_graph(edges, params)
    assert graph.number_of_nodes() == 3
    assert graph.number_of_edges() == 2
    assert pairs[0][1] == pairs[1][0], "the shared endpoint must be one node"


def test_closed_loop_is_skipped(params):
    edges = frame([
        {"id": 1, "lts": 2,
         "geometry": LineString([at(0), at(0.001), at(0.001, 0.001), at(0)])},
    ])
    graph, _, pairs = net.build_graph(edges, params)
    assert pairs[0] is None
    assert graph.number_of_edges() == 0


# ---------------------------------------------------------------------------
#  Islands
# ---------------------------------------------------------------------------

def _chain(n, lts, start=0.0, y=0.0, id_base=0):
    return [
        {
            "id": id_base + i,
            "lts": lts,
            "road_name": "TEST ST",
            "geometry": LineString([at(start + i * 0.001, y), at(start + (i + 1) * 0.001, y)]),
        }
        for i in range(n)
    ]


def test_islands_split_on_a_high_stress_gap(params):
    """Two low-stress chains separated by an LTS 4 segment are two islands."""
    rows = _chain(3, 2, start=0.0, id_base=0)
    rows.append({"id": 100, "lts": 4, "road_name": "BIG RD",
                 "geometry": LineString([at(0.003), at(0.004)])})
    rows += _chain(3, 2, start=0.004, id_base=200)
    edges = frame(rows)

    graph, _, pairs = net.build_graph(edges, params)
    labelled, islands = net.label_islands(edges, pairs, params)

    low = labelled[labelled["lts"] <= 2]
    assert low["island"].nunique() == 2
    assert labelled.loc[labelled["id"] == 100, "island"].iloc[0] == net.NO_ISLAND
    assert len(islands) == 2


def test_islands_are_ranked_by_mileage_not_segment_count(params):
    """Segment length spans two orders of magnitude in the real data, so ranking
    by count would mis-order the islands."""
    # island A: 2 long segments; island B: 5 very short ones
    rows = [
        {"id": 1, "lts": 2, "road_name": "LONG ST",
         "geometry": LineString([at(0), at(0.05)])},
        {"id": 2, "lts": 2, "road_name": "LONG ST",
         "geometry": LineString([at(0.05), at(0.10)])},
    ]
    rows += [
        {"id": 10 + i, "lts": 2, "road_name": "SHORT ST",
         "geometry": LineString([at(0.5 + i * 0.0001), at(0.5 + (i + 1) * 0.0001)])}
        for i in range(5)
    ]
    edges = frame(rows)
    _, _, pairs = net.build_graph(edges, params)
    labelled, islands = net.label_islands(edges, pairs, params)

    assert islands["island"].iloc[0] == 0
    assert islands["miles"].iloc[0] > islands["miles"].iloc[1]
    assert islands["segments"].iloc[0] == 2, "the 2-segment island is larger by mileage"
    assert labelled.loc[labelled["id"] == 1, "island"].iloc[0] == 0


def test_small_components_are_labelled_but_flagged_minor(params):
    """Every component gets an island id. The old min_cluster_segments filter
    left 8.7% of low-stress mileage unlabelled, so roughly one in eleven
    low-stress clicks would have had no island to show."""
    rows = _chain(5, 2, start=0.0, id_base=0)          # major
    rows += _chain(2, 2, start=0.5, id_base=50)        # minor (below the floor)
    edges = frame(rows)
    _, _, pairs = net.build_graph(edges, params)
    labelled, islands = net.label_islands(edges, pairs, params)

    assert (labelled["island"] != net.NO_ISLAND).all(), "no low-stress segment unlabelled"
    assert len(islands) == 2
    assert islands["major"].tolist() == [True, False]


# ---------------------------------------------------------------------------
#  Barriers
# ---------------------------------------------------------------------------

def _two_islands_with_crossings(names):
    """Two 3-segment low-stress chains joined by one high-stress segment per name."""
    rows = _chain(3, 2, start=0.0, id_base=0)
    rows += _chain(3, 2, start=0.004, id_base=200)
    for k, name in enumerate(names):
        rows.append({
            "id": 500 + k, "lts": 3, "road_name": name,
            "geometry": LineString([at(0.003), at(0.004)]),
        })
    return frame(rows)


def test_barrier_is_found_between_two_islands(params):
    edges = _two_islands_with_crossings(["GAP RD"])
    _, _, pairs = net.build_graph(edges, params)
    labelled, islands = net.label_islands(edges, pairs, params)
    barriers = net.rank_barriers(labelled, pairs, islands, params)

    assert len(barriers) == 1
    r = barriers.iloc[0]
    assert r["name"] == "GAP RD"
    assert r["miles_unlocked"] == pytest.approx(min(r["island_a_miles"], r["island_b_miles"]))


def test_miles_unlocked_uses_the_smaller_island(params):
    """Merging a large island with a small stub unlocks the stub's mileage, not
    the large island's."""
    rows = _chain(8, 2, start=0.0, id_base=0)
    rows += _chain(3, 2, start=0.009, id_base=200)
    rows.append({"id": 500, "lts": 4, "road_name": "GAP RD",
                 "geometry": LineString([at(0.008), at(0.009)])})
    edges = frame(rows)
    _, _, pairs = net.build_graph(edges, params)
    labelled, islands = net.label_islands(edges, pairs, params)
    barriers = net.rank_barriers(labelled, pairs, islands, params)

    r = barriers.iloc[0]
    smaller = min(r["island_a_miles"], r["island_b_miles"])
    assert r["miles_unlocked"] == pytest.approx(smaller)
    assert r["miles_unlocked"] < max(r["island_a_miles"], r["island_b_miles"])


def test_crossings_of_the_same_pair_are_alternatives_not_additive(params):
    """Three streets crossing between the same two islands are alternatives:
    treating any one merges the pair. Summing their unlocked mileage would
    triple-count the same miles."""
    edges = _two_islands_with_crossings(["FIRST RD", "SECOND RD", "THIRD RD"])
    _, _, pairs = net.build_graph(edges, params)
    labelled, islands = net.label_islands(edges, pairs, params)
    barriers = net.rank_barriers(labelled, pairs, islands, params)

    assert len(barriers) == 3
    assert int(barriers["best_for_pair"].sum()) == 1, "only one is the best for the pair"
    assert barriers.loc[barriers["best_for_pair"], "alternatives"].iloc[0] == 2

    unlocked = barriers.loc[barriers["best_for_pair"], "miles_unlocked"].sum()
    assert unlocked < barriers["miles_unlocked"].sum(), "naive summing over-counts"


def test_no_barrier_within_a_single_island(params):
    """A high-stress segment whose ends are on the same island bridges nothing."""
    rows = _chain(3, 2, start=0.0, id_base=0)
    rows.append({"id": 500, "lts": 4, "road_name": "PARALLEL RD",
                 "geometry": LineString([at(0.0), at(0.003)])})
    edges = frame(rows)
    _, _, pairs = net.build_graph(edges, params)
    labelled, islands = net.label_islands(edges, pairs, params)
    barriers = net.rank_barriers(labelled, pairs, islands, params)
    assert len(barriers) == 0


def test_empty_islands_yields_no_barriers(params):
    edges = frame([{"id": 1, "lts": 4, "road_name": "X",
                    "geometry": LineString([at(0), at(0.001)])}])
    _, _, pairs = net.build_graph(edges, params)
    labelled, islands = net.label_islands(edges, pairs, params)
    barriers = net.rank_barriers(labelled, pairs, islands, params)
    assert len(barriers) == 0

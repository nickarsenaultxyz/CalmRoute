"""Graph construction, splitting, island labelling and barrier ranking.

Uses small synthetic networks so each behaviour is isolated. The real-data
figures are pinned by the aggregate-stability test instead.
"""

from __future__ import annotations

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString, MultiLineString, Point

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


def empty_connectors(crs="EPSG:4326"):
    return gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs=crs)


def with_reviewed_links(params, specs):
    tree = params.tree
    tree["network"]["reviewed_street_links"] = specs
    return params_mod.Params(tree)


def with_reviewed_closures(params, specs):
    tree = params.tree
    tree["network"]["reviewed_street_closures"] = specs
    return params_mod.Params(tree)


def with_exact_junctions(params, specs):
    tree = params.tree
    tree["network"]["reviewed_exact_junctions"] = specs
    return params_mod.Params(tree)


# ---------------------------------------------------------------------------
#  Cutting
# ---------------------------------------------------------------------------

def test_cut_splits_at_one_point():
    line = LineString([(0, 0), (10, 0)])
    pieces = net.cut_line(line, [4.0])
    assert len(pieces) == 2
    assert pieces[0].length == pytest.approx(4.0)
    assert pieces[1].length == pytest.approx(6.0)


def test_cut_preserves_total_length():
    line = LineString([(0, 0), (5, 0), (5, 5), (10, 5)])
    pieces = net.cut_line(line, [2.0, 7.0, 11.0])
    assert len(pieces) == 4
    assert sum(p.length for p in pieces) == pytest.approx(line.length)


def test_cut_pieces_join_end_to_end():
    """Adjacent pieces must share an exact coordinate, or splitting would create
    the very disconnection it exists to prevent."""
    line = LineString([(0, 0), (10, 0), (10, 10)])
    pieces = net.cut_line(line, [3.0, 14.0])
    for a, b in zip(pieces, pieces[1:]):
        assert a.coords[-1] == b.coords[0]


def test_cut_ignores_degenerate_distances():
    line = LineString([(0, 0), (10, 0)])
    assert len(net.cut_line(line, [])) == 1


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
    """An unnormalized loop remains defensively excluded by build_graph."""
    edges = frame([
        {"id": 1, "lts": 2,
         "geometry": LineString([at(0), at(0.001), at(0.001, 0.001), at(0)])},
    ])
    graph, _, pairs = net.build_graph(edges, params)
    assert pairs[0] is None
    assert graph.number_of_edges() == 0


def test_closed_street_is_split_into_a_three_edge_cycle(params):
    ring = LineString([
        at(0), at(0.001), at(0.001, 0.001), at(0, 0.001), at(0),
    ])
    streets = frame([{"id": 1, "lts": 2, "geometry": ring}])

    normalized = net.split_for_connectors(streets, empty_connectors(), params)
    graph, _, pairs = net.build_graph(normalized, params)

    assert len(normalized) == 3
    assert set(normalized["id"]) == {
        1, net.RING_SPLIT_ID_BASE, net.RING_SPLIT_ID_BASE + 1,
    }
    assert sum(geom.length for geom in normalized.geometry) == pytest.approx(
        ring.length
    )
    assert all(pair is not None for pair in pairs)
    assert graph.number_of_nodes() == 3
    assert graph.number_of_edges() == 3


def test_exact_endpoint_to_interior_vertex_is_split(params):
    streets = frame([
        {
            "id": 1,
            "lts": 2,
            "geometry": LineString([at(0), at(0.001), at(0.002)]),
        },
        {
            "id": 2,
            "lts": 2,
            "geometry": LineString([at(0.001, -0.001), at(0.001)]),
        },
    ])

    configured = with_exact_junctions(params, [{
        "name": "Synthetic exact junction",
        "target_street_id": 1,
        "target_street_name": "THROUGH ST",
        "endpoint_street_id": 2,
        "endpoint_street_name": "STUB ST",
        "point": list(at(0.001)),
        "why": "Synthetic test fixture",
    }])
    streets["road_name"] = ["THROUGH ST", "STUB ST"]

    normalized = net.split_for_connectors(
        streets, empty_connectors(), configured
    )
    graph, _, pairs = net.build_graph(normalized, configured)

    assert len(normalized) == 3
    assert net.JUNCTION_SPLIT_ID_BASE in set(normalized["id"])
    stub_pair = pairs[normalized.index[normalized["id"] == 2][0]]
    long_pairs = [
        pairs[i] for i in normalized.index[normalized["id"] != 2]
    ]
    shared = set(stub_pair) & set(long_pairs[0]) & set(long_pairs[1])
    assert len(shared) == 1
    assert graph.number_of_edges() == 3


def test_unreviewed_exact_interior_junction_fails_closed(params):
    streets = frame([
        {
            "id": 1,
            "road_name": "THROUGH ST",
            "lts": 2,
            "geometry": LineString([at(0), at(0.001), at(0.002)]),
        },
        {
            "id": 2,
            "road_name": "STUB ST",
            "lts": 2,
            "geometry": LineString([at(0.001, -0.001), at(0.001)]),
        },
    ])
    configured = with_exact_junctions(params, [])

    with pytest.raises(net.NetworkError, match="need bridge/layer review"):
        net.split_for_connectors(streets, empty_connectors(), configured)


def test_tiny_ring_cannot_bypass_collapsed_node_invariant(params):
    tiny = LineString([
        at(0), at(0.000004), at(0.000004, 0.000004), at(0),
    ])
    streets = frame([{"id": 1, "lts": 2, "geometry": tiny}])

    with pytest.raises(net.NetworkError, match="collapses entirely"):
        net.split_for_connectors(streets, empty_connectors(), params)


def test_unsplit_subprecision_line_cannot_bypass_invariant(params):
    streets = frame([{
        "id": 1,
        "lts": 2,
        "geometry": LineString([at(0), at(0.000004)]),
    }])

    with pytest.raises(net.NetworkError, match="still collapse"):
        net.split_for_connectors(streets, empty_connectors(), params)


def test_collapsed_near_end_cut_keeps_parent_id_and_geometry(params):
    """A near-end cut must not make the stable source SCLINK disappear."""
    junction = at(0.000004)
    streets = frame([
        {
            "id": 1,
            "lts": 2,
            "geometry": LineString([at(0), junction, at(0.001)]),
        },
        {
            "id": 2,
            "lts": 2,
            "geometry": LineString([at(0, -0.001), junction]),
        },
    ])

    normalized = net.split_for_connectors(streets, empty_connectors(), params)
    _, _, pairs = net.build_graph(normalized, params)

    assert set(normalized["id"]) == {1, 2}
    assert all(pair is not None for pair in pairs)
    assert sum(geom.length for geom in normalized.geometry) == pytest.approx(
        sum(geom.length for geom in streets.geometry)
    )


def test_connector_cuts_in_one_published_node_merge_without_geometry_loss(params):
    street = LineString([at(0), at(0.002)])
    streets = frame([{"id": 1, "lts": 2, "geometry": street}])
    requested = gpd.GeoSeries(
        [Point(at(0.001)), Point(at(0.001004))], crs=4326
    ).to_crs(params["meta.crs_working"])
    connectors = frame([
        {
            "split_street_id": 1,
            "split_x": point.x,
            "split_y": point.y,
            "geometry": LineString([at(0), at(0.0001)]),
        }
        for point in requested
    ])

    normalized = net.split_for_connectors(streets, connectors, params)
    graph, _, pairs = net.build_graph(normalized, params)

    assert len(normalized) == 2
    assert set(normalized["id"]) == {1, net.SPLIT_ID_BASE}
    assert sum(geom.length for geom in normalized.geometry) == pytest.approx(
        street.length
    )
    assert all(pair is not None for pair in pairs)
    assert graph.number_of_edges() == 2


# ---------------------------------------------------------------------------
#  Individually reviewed street closures and links
# ---------------------------------------------------------------------------

def reviewed_closure_spec(**overrides):
    spec = {
        "name": "Test street closure",
        "id": 10,
        "street_name": "FIRST ST",
        "min_length_m": 80.0,
        "max_length_m": 100.0,
        "why": "Synthetic test fixture",
    }
    spec.update(overrides)
    return spec


def test_reviewed_street_closure_survives_topology_splitting(params):
    streets = frame([{
        "id": 10,
        "road_name": "FIRST ST",
        "lts": 1,
        "geometry": LineString([at(0), at(0.001)]),
    }])
    configured = with_reviewed_closures(
        params, [reviewed_closure_spec()]
    )

    closed = net.apply_reviewed_street_closures(streets, configured)
    metric_midpoint = gpd.GeoSeries(
        [Point(at(0.0005))], crs=4326
    ).to_crs(params["meta.crs_working"]).iloc[0]
    connectors = frame([{
        "split_street_id": 10,
        "split_x": metric_midpoint.x,
        "split_y": metric_midpoint.y,
        "geometry": LineString([at(0), at(0.0001)]),
    }])
    pieces = net.split_for_connectors(closed, connectors, configured)

    assert len(pieces) == 2
    assert not pieces["road_bike_ok"].any()
    assert pieces["access_reviewed"].all()


def test_partial_street_closure_keeps_public_remainder_routable(params):
    source = LineString([at(0), at(0.001)])
    streets = frame([{
        "id": 10,
        "road_name": "FIRST ST",
        "lts": 1,
        "geometry": source,
    }])
    spec = reviewed_closure_spec(
        boundary=list(at(0.0004)),
        closed_from="start",
        closed_endpoint=list(at(0)),
        max_boundary_m=0.5,
        max_endpoint_m=0.5,
        min_closed_length_m=30.0,
        max_closed_length_m=40.0,
        public_piece_id=725_009_001,
    )
    configured = with_reviewed_closures(params, [spec])

    split = net.apply_reviewed_street_closures(streets, configured)

    assert set(split["id"]) == {10, 725_009_001}
    closed = split.loc[split["id"] == 10].iloc[0]
    public = split.loc[split["id"] == 725_009_001].iloc[0]
    assert not bool(closed["road_bike_ok"])
    assert bool(closed["access_reviewed"])
    assert bool(public["road_bike_ok"])
    assert not bool(public["access_reviewed"])
    assert closed.geometry.coords[-1] == public.geometry.coords[0]
    assert sum(piece.length for piece in split.geometry) == pytest.approx(
        source.length
    )


@pytest.mark.parametrize(
    "override, message",
    [
        ({"id": 999}, "street id 999 is unavailable"),
        ({"street_name": "WRONG ST"}, "expected 'WRONG ST'"),
        ({"max_length_m": 85.0}, "expected 80.0-85.0 m"),
        ({
            "boundary": list(at(0.01)),
            "closed_from": "start",
            "closed_endpoint": list(at(0)),
            "max_boundary_m": 0.5,
            "max_endpoint_m": 0.5,
            "min_closed_length_m": 30.0,
            "max_closed_length_m": 40.0,
            "public_piece_id": 725_009_001,
        }, "boundary is"),
    ],
)
def test_reviewed_street_closure_quality_gates(params, override, message):
    streets = frame([{
        "id": 10,
        "road_name": "FIRST ST",
        "lts": 1,
        "geometry": LineString([at(0), at(0.001)]),
    }])
    configured = with_reviewed_closures(
        params, [reviewed_closure_spec(**override)]
    )

    with pytest.raises(net.NetworkError, match=message):
        net.apply_reviewed_street_closures(streets, configured)


def reviewed_link_spec(**overrides):
    spec = {
        "id": 730_009_001,
        "name": "Test reviewed gap",
        "road_name": "FIRST ST / SECOND ST",
        "street_ids": [10, 20],
        "street_names": ["FIRST ST", "SECOND ST"],
        "geometry": [at(0.001), at(0.0011, 0.00003), at(0.0012)],
        "max_endpoint_m": 3.0,
        "max_length_m": 30.0,
        "lts": 2,
        "why": "Synthetic test fixture",
    }
    spec.update(overrides)
    return spec


def reviewed_link_streets():
    return frame([
        {
            "id": 10, "road_name": "FIRST ST", "lts": 1, "fac": "none",
            "geometry": LineString([at(0), at(0.001)]),
        },
        {
            "id": 20, "road_name": "SECOND ST", "lts": 2, "fac": "none",
            "geometry": LineString([at(0.0012), at(0.0022)]),
        },
        {
            "id": 30, "road_name": "UNLISTED ST", "lts": 1, "fac": "none",
            "geometry": LineString([at(0.0011, 0.0001), at(0.0011, 0.0002)]),
        },
    ])


def test_reviewed_street_link_joins_only_the_pinned_endpoints(params):
    streets = reviewed_link_streets()
    configured = with_reviewed_links(params, [reviewed_link_spec()])

    linked = net.add_reviewed_street_links(streets, configured)
    graph, _, pairs = net.build_graph(linked, configured)

    bridge_index = linked.index[linked["id"] == 730_009_001][0]
    bridge = linked.loc[bridge_index]
    assert bridge.geometry.coords[0] == streets.geometry.iloc[0].coords[-1]
    assert bridge.geometry.coords[-1] == streets.geometry.iloc[1].coords[0]
    assert bridge["source"] == "osm"
    assert bridge["osm_role"] == "reviewed_street_link"
    assert bool(bridge["connector_reviewed"]) is True
    assert bridge["fac"] == "none"
    assert bridge["lts"] == 2
    assert bool(bridge["road_bike_ok"]) is True

    first_pair, second_pair, third_pair = pairs[:3]
    bridge_pair = pairs[bridge_index]
    assert first_pair[1] == bridge_pair[0]
    assert second_pair[0] == bridge_pair[1]
    assert not (set(third_pair) & set(bridge_pair)), \
        "a nearby unlisted endpoint must remain disconnected"
    assert graph.number_of_nodes() == 6
    assert graph.number_of_edges() == 4


@pytest.mark.parametrize(
    "override, message",
    [
        ({"street_ids": [10, 999]}, "street id 999 is unavailable"),
        ({"street_names": ["WRONG ST", "SECOND ST"]}, "expected 'WRONG ST'"),
        ({"geometry": [at(0.01), at(0.011)]}, "configured end is"),
        ({"max_length_m": 5.0}, "geometry is"),
    ],
)
def test_reviewed_street_link_quality_gates(params, override, message):
    configured = with_reviewed_links(
        params, [reviewed_link_spec(**override)]
    )
    with pytest.raises(net.NetworkError, match=message):
        net.add_reviewed_street_links(reviewed_link_streets(), configured)


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

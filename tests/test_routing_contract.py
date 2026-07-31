"""What the browser router needs from graph.json.

Routing happens client-side, so nothing in Python exercises it. These assert the
shape and the invariants the JavaScript relies on, so a change to export.py
fails here rather than silently producing a router that returns no routes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

DATA = Path("data")

pytestmark = pytest.mark.skipif(
    not (DATA / "graph.json").exists(),
    reason="data/ not built; run `make build`",
)


@pytest.fixture(scope="module")
def graph():
    return json.loads((DATA / "graph.json").read_text())


@pytest.fixture(scope="module")
def features_by_id():
    out = {}
    for name in ("network.geojson", "context.geojson", "residential.geojson"):
        for feature in json.loads((DATA / name).read_text())["features"]:
            out[feature["id"]] = feature
    return out


def test_edge_field_order_matches_the_client(graph):
    """js/lib/graph.js destructures [u, v, id, mi, lts] positionally."""
    assert graph["edge_fields"] == ["u", "v", "id", "miles", "lts"]


def test_every_edge_endpoint_is_a_real_node(graph):
    n = len(graph["nodes"])
    for u, v, *_ in graph["edges"]:
        assert 0 <= u < n and 0 <= v < n


def test_no_self_loops(graph):
    """A self-loop connects nothing and would waste a heap slot per visit."""
    assert not [e for e in graph["edges"] if e[0] == e[1]]


def test_edge_lengths_are_positive(graph):
    """A zero-cost edge lets Dijkstra settle nodes in an arbitrary order."""
    assert all(e[3] > 0 for e in graph["edges"])


def test_lts_values_have_a_routing_penalty(graph):
    """Every ROUTE_LEVELS penalty table in js/config.js covers 0-4. An unmapped
    value is treated as impassable, so the edge would silently vanish from the
    network at every comfort setting rather than being routed over."""
    assert {e[4] for e in graph["edges"]} <= {0, 1, 2, 3, 4}


def test_edge_ids_resolve_to_drawable_features(graph):
    """The route is drawn from geometry already in memory, keyed by feature id.
    An id with no feature renders as a gap in the line -- which is exactly the
    bug that appeared when the quiet-street layer was not loaded."""
    ids = set()
    for name in ("network.geojson", "context.geojson", "residential.geojson"):
        for f in json.loads((DATA / name).read_text())["features"]:
            ids.add(f["id"])

    missing = [e[2] for e in graph["edges"] if e[2] not in ids]
    assert not missing, f"{len(missing)} routable edges have no drawable geometry"


def test_every_drawn_edge_lands_exactly_on_its_graph_nodes(graph, features_by_id):
    """Every transition in a browser route must be spatially continuous.

    Sharing a graph node is not enough if the corresponding drawn lines end at
    different coordinates: the search would succeed while the route appears to
    jump across a gap. Audit every published edge against the node coordinates
    the router actually uses.
    """
    bad = []
    for u, v, edge_id, *_ in graph["edges"]:
        coords = features_by_id[edge_id]["geometry"]["coordinates"]
        drawn = {tuple(coords[0]), tuple(coords[-1])}
        expected = {tuple(graph["nodes"][u]), tuple(graph["nodes"][v])}
        if drawn != expected:
            bad.append(edge_id)

    assert not bad, f"{len(bad)} route edges do not line up with their graph nodes"


def test_trails_can_be_joined_partway_along(graph):
    """A path must not be one long edge.

    Off-road paths used to attach to the street network only at their two
    extremities, which made each one a single graph edge -- enterable at one end
    and leavable at the other, and nowhere else. 34 of 39 path miles were
    unusable for anything but end-to-end travel, and routes went the long way
    round rather than use a trail passing metres from the destination.
    """
    fac = {}
    for name in ("network.geojson", "context.geojson", "residential.geojson"):
        for f in json.loads((DATA / name).read_text())["features"]:
            fac[f["id"]] = f["properties"].get("fac", 0)

    degree: dict[int, int] = {}
    for u, v, *_ in graph["edges"]:
        degree[u] = degree.get(u, 0) + 1
        degree[v] = degree.get(v, 0) + 1

    path_edges = [e for e in graph["edges"] if fac.get(e[2]) == 6]
    assert path_edges, "no shared-use paths in the routing graph"

    # A junction is an endpoint shared with something else.
    joined = [e for e in path_edges
              if degree.get(e[0], 0) > 1 and degree.get(e[1], 0) > 1]
    share = len(joined) / len(path_edges)
    assert share > 0.6, (
        f"only {share:.0%} of path edges are joined at both ends; "
        "trails are probably attaching at their extremities only"
    )

    longest = max(e[3] for e in path_edges)
    assert longest < 3.0, (
        f"longest single path edge is {longest:.2f} mi with no intermediate "
        "junction; a rider cannot join it partway"
    )


def test_no_connector_is_longer_than_its_reviewed_search_radius(graph):
    """A connector is a short link across a verge, not a shortcut.

    `project` is ambiguous where a path doubles back on itself, so deriving the
    attachment point by round-tripping through an along-distance once produced a
    1,476 m "connector" -- a phantom LTS 1 teleport across the city, free to
    ride and invisible in every aggregate.
    """
    import tomllib

    with open("params.toml", "rb") as fh:
        conflation = tomllib.load(fh)["conflation"]
    max_m = float(conflation["connector_max_m"])
    reviewed_max_m = max(
        float(row["max_m"]) for row in conflation["reviewed_connectors"])

    props = {}
    for name in ("network.geojson", "context.geojson", "residential.geojson"):
        for f in json.loads((DATA / name).read_text())["features"]:
            props[f["id"]] = f["properties"]

    connectors = [
        e for e in graph["edges"]
        if props.get(e[2], {}).get("fac") == 7
    ]
    assert connectors, "no connectors in the graph"

    ordinary = [e for e in connectors if not props[e[2]].get("rv")]
    reviewed = [e for e in connectors if props[e[2]].get("rv")]
    longest_m = max(e[3] for e in ordinary) * 1609.344
    assert longest_m <= max_m * 1.1, (
        f"longest automatic connector is {longest_m:.0f} m "
        f"against a {max_m:.0f} m radius"
    )
    assert len(reviewed) == len(conflation["reviewed_connectors"])
    longest_reviewed_m = max(e[3] for e in reviewed) * 1609.344
    assert longest_reviewed_m <= reviewed_max_m * 1.01, (
        f"longest reviewed connector is {longest_reviewed_m:.0f} m "
        f"against a {reviewed_max_m:.0f} m reviewed cap"
    )


def test_fine_arts_reviewed_connectors_join_paths_to_streets(graph):
    """The two requested campus links must be genuine graph junctions."""
    features = {}
    for name in ("network.geojson", "context.geojson", "residential.geojson"):
        for feature in json.loads((DATA / name).read_text())["features"]:
            features[feature["id"]] = feature

    reviewed = [
        feature for feature in features.values()
        if feature["properties"].get("fac") == 7
        and feature["properties"].get("rv") == 1
    ]
    assert len(reviewed) == 2

    adjacency = {}
    for u, v, edge_id, *_ in graph["edges"]:
        adjacency.setdefault(u, []).append(edge_id)
        adjacency.setdefault(v, []).append(edge_id)

    expected_sources = {
        (-84.50717, 38.04241),
        (-84.50949, 38.04007),
    }
    actual_sources = set()
    target_names = set()
    for connector in reviewed:
        coords = [tuple(point) for point in connector["geometry"]["coordinates"]]
        source = min(
            coords,
            key=lambda point: min(
                (point[0] - expected[0]) ** 2
                + (point[1] - expected[1]) ** 2
                for expected in expected_sources
            ),
        )
        actual_sources.add(source)

        u = connector["properties"]["u"]
        v = connector["properties"]["v"]
        incident = {
            node: [
                features[edge_id]["properties"]
                for edge_id in adjacency[node]
                if edge_id != connector["id"]
            ]
            for node in (u, v)
        }
        assert any(
            any(props.get("fac") == 6 for props in rows)
            for rows in incident.values()
        ), "reviewed connector does not touch an off-road path"
        street_rows = [
            props for rows in incident.values() for props in rows
            if props.get("fac") != 7 and props.get("kind") == 0
        ]
        target_names.update(
            props["nm"] for props in street_rows if props.get("nm")
        )

    assert actual_sources == expected_sources
    assert {"S Mill St", "Colfax St"} <= target_names


def test_the_low_stress_network_is_actually_routable(graph):
    """Guards the premise of the whole feature: if LTS 1-2 edges did not form
    substantial connected components, "plan a comfortable route" could never
    succeed and the failure screen would be the only screen."""
    parent = list(range(len(graph["nodes"])))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for u, v, _id, _mi, lts in graph["edges"]:
        if lts in (1, 2):
            a, b = find(u), find(v)
            if a != b:
                parent[a] = b

    sizes: dict[int, int] = {}
    for i in range(len(parent)):
        r = find(i)
        sizes[r] = sizes.get(r, 0) + 1

    largest = max(sizes.values())
    assert largest > 500, (
        f"largest low-stress component is only {largest} nodes; "
        "comfortable routing would almost always fail"
    )

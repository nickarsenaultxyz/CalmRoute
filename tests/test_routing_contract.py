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
    """js/lib/dijkstra.js PENALTY covers 0-4. An unmapped value yields an
    undefined cost, which silently poisons every path through that edge."""
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

"""The frontend's assumptions about the exported data, asserted in CI-able form.

The JavaScript reads specific property names off specific files. Nothing in
Python enforces that, so a rename in export.py would break the map silently and
only be noticed by opening it. These tests close that gap.

They skip when data/ has not been built, so a fresh clone can still run the
suite.
"""

from __future__ import annotations

import heapq
import json
from pathlib import Path

import pytest
from shapely.geometry import Polygon, shape

from lexbike.params import load as load_params

DATA = Path("data")

pytestmark = pytest.mark.skipif(
    not (DATA / "manifest.json").exists(),
    reason="data/ not built; run `make build`",
)


def load(name):
    return json.loads((DATA / name).read_text())


@pytest.fixture(scope="module")
def manifest():
    return load("manifest.json")


@pytest.fixture(scope="module")
def features():
    out = []
    for name in ("network.geojson", "context.geojson", "residential.geojson"):
        out.extend(load(name)["features"])
    return out


# --------------------------------------------------------------------------
#  manifest — js/data.js drives every other fetch from this
# --------------------------------------------------------------------------

def test_manifest_lists_every_file_the_app_fetches(manifest):
    for key in ("network", "context", "residential", "stats", "methodology",
                "graph", "planned", "council"):
        assert key in manifest["files"], f"js/data.js fetches manifest.files.{key}"
        assert (DATA / manifest["files"][key]).exists()


def test_manifest_has_an_initial_view(manifest):
    assert len(manifest["center"]) == 2
    assert isinstance(manifest["zoom"], (int, float))
    assert manifest["version"], "js/data.js appends ?v=<version> for cache busting"
    assert manifest["version"] == manifest["generated"], (
        "the full build timestamp must be the cache key; a date alone collides "
        "when two source refreshes deploy on the same day"
    )


def test_router_waits_for_every_geometry_layer_before_drawing():
    """A graph result is not drawable until all three feature files are loaded.

    The context layer used to load in the background while routing waited only
    for graph.json and residential.geojson. A quick route could therefore
    contain valid busy-road IDs whose geometry was not yet in featuresById;
    drawRoute silently skipped them and painted disconnected fragments.
    """
    source = Path("js/main.js").read_text()
    ensure_graph = source.split(
        "async function ensureGraph()", 1
    )[1].split(
        "async function recomputeRoute()", 1
    )[0]
    assert "loadGraph(app.manifest)" in ensure_graph
    assert "ensureContext()" in ensure_graph
    assert "app.residential.ensure()" in ensure_graph
    assert "missingIds.length" in source
    assert "route feature geometry missing" in source


def test_router_uses_one_cache_key_for_its_graph_runtime():
    """A returning browser must not combine old and new graph modules.

    GitHub Pages caches assets for ten minutes. Importing ``graph.js`` once
    with a version and once without one let the new route UI receive an older
    module that had no ``snapToNetwork`` export.
    """
    main = Path("js/main.js").read_text()
    index = Path("index.html").read_text()

    assert "import('./lib/graph.js')" not in main
    assert "from './lib/graph.js?v=20260731-routing-runtime'" in main
    assert 'src="./js/main.js?v=20260731-routing-runtime"' in index


def test_location_search_is_submit_only_bounded_and_rate_limited():
    """The route geocoder must obey the public Nominatim usage policy.

    Searches are explicit form submissions, never autocomplete requests. Every
    query stays inside the published Lexington boundary, repeated queries are
    cached, and network starts are kept more than one second apart.
    """
    view = Path("js/views/route.js").read_text()
    geocoder = Path("js/lib/geocode.js").read_text()
    main = Path("js/main.js").read_text()

    assert "addEventListener('submit'" in view
    assert "addEventListener('input'" in view
    assert "handlers.onSearch" not in view.split(
        "addEventListener('input'", 1
    )[1].split("});", 1)[0]
    assert "MIN_REQUEST_INTERVAL_MS = 1100" in geocoder
    assert "cache.has(key)" in geocoder
    assert "viewbox" in geocoder
    assert "'bounded', '1'" in geocoder
    assert "app.manifest.bbox" in main
    assert "OpenStreetMap" in view and "Nominatim" in view


def test_calmroute_home_uses_maplibre_and_real_graph_results_only():
    """The supplied design is a layout reference, not a source of route data.

    Keep the production renderer on MapLibre and calculate every comparison
    card from the published graph. The reference's sample rider reports,
    accounts, climb figures, and Leaflet runtime must never leak into the app.
    """
    index = Path("index.html").read_text()
    state = Path("js/lib/urlstate.js").read_text()
    main = Path("js/main.js").read_text()
    view = Path("js/views/route.js").read_text()

    assert "view: 'route'" in state
    assert "residential: true" in state
    assert "maplibre-gl-5.24.0" in index
    assert "leaflet" not in index.lower()

    for key, mode in (
        ("calmest", "quiet"),
        ("balanced", "balanced"),
        ("fastest", "shortest"),
    ):
        assert f"key: '{key}'" in main
        assert f"mode: '{mode}'" in main
    assert "routeBetweenSnaps(g, dj, snapA, snapB, spec.mode)" in main
    assert "routeSegments(raw, snapA, snapB)" in main

    for sample_only in ("Rider reports", "Climb", "Start ride", "avatar"):
        assert sample_only not in view


def test_complete_street_network_loads_immediately_by_default():
    data = Path("js/data.js").read_text()
    main = Path("js/main.js").read_text()

    eager_branch = data.split("if (eager)", 1)[1].split(
        "return { ensure }", 1
    )[0]
    assert "ensure()" in eager_branch
    assert "requestIdleCallback" not in eager_branch
    assert "eager: app.state.residential" in main


def test_all_application_lines_are_solid():
    """Stress is communicated by colour, without dashed network fragments."""
    config = Path("js/config.js").read_text()
    layers = Path("js/layers.js").read_text()
    styles = Path("assets/app.css").read_text()

    assert "dash:" not in config
    assert "line-dasharray" not in layers
    assert "stroke-dasharray" not in layers
    assert "dashed" not in styles


def test_uk_campus_walkways_render_thinner_than_bike_infrastructure():
    layers = Path("js/layers.js").read_text()
    assert "CAMPUS_WALKWAY_WIDTH_SCALE = 0.45" in layers
    width = layers.split("function widthExpr", 1)[1].split(
        "export function addSources", 1
    )[0]
    assert "'campus_path'" in width
    assert "facilityScale" in width


# --------------------------------------------------------------------------
#  stats — js/views/legend.js reads these exact paths
# --------------------------------------------------------------------------

def test_stats_paths_the_legend_reads(manifest):
    s = load(manifest["files"]["stats"])
    assert all("lts" in r and "miles" in r for r in s["by_lts"])
    for key in ("miles",):
        assert s["low_stress"][key] is not None
    assert s["ridable_lts3"]["miles"] is not None
    # detail.js dates the traffic figure from this; an undated count would
    # imply it is current, and these span 2001-2024.
    assert s["data_sources"]["aadt_count_years"]["median"]
    assert s["data_sources"]["aadt_model_segments"] > 0
    assert s["data_sources"]["aadt_model_pct"] > 0


# --------------------------------------------------------------------------
#  features
# --------------------------------------------------------------------------

def test_every_feature_has_a_top_level_numeric_id(features):
    """MapLibre `promoteId: 'id'` and the `?sel=` deep link both need this."""
    assert all(isinstance(f["id"], int) for f in features)


def test_feature_ids_are_unique_across_both_layers(features):
    ids = [f["id"] for f in features]
    assert len(ids) == len(set(ids))


def test_properties_the_ui_reads_are_present(features):
    present = {k for f in features for k in f["properties"]}
    for key in ("lts", "fac", "kind", "nm", "sp", "ad", "mi", "cf", "basis"):
        assert key in present, f"the UI reads properties.{key}"


def test_enabled_osm_build_preserves_provenance_and_access_policy(manifest, features):
    stats = load(manifest["files"]["stats"])
    if not stats.get("osm_paths", {}).get("enabled"):
        pytest.skip("OSM supplement disabled for this build")

    osm = [f for f in features if f["properties"].get("src") == "osm"]
    paths = [
        f for f in osm
        if f["properties"].get("osm_role") == "path"
    ]
    access = [
        f for f in osm
        if f["properties"].get("osm_role") == "access"
    ]
    reviewed = [
        f for f in osm
        if f["properties"].get("osm_role") == "reviewed_street"
    ]
    campus = [
        f for f in osm
        if f["properties"].get("osm_role") == "campus_path"
    ]
    assert paths, "an OSM-enabled build must export auditable path provenance"
    assert campus, "UK campus walkways must reach the published routing graph"
    assert access, "explicitly bicycle-authorized access roads must reach the graph"
    assert reviewed, "reviewed missing streets must reach the graph"
    access_rating = stats["osm_paths"]["access_roads"]["rating"]
    assert access_rating == 2
    assert all(f["properties"]["lts"] == access_rating for f in access)
    assert all(f["properties"]["kind"] == 0 for f in access)

    names = {f["properties"].get("nm") for f in access}
    assert "Baptist Health Entrance 1" in names
    assert stats["osm_paths"]["access_roads"]["segments"] == len(access)
    assert stats["osm_paths"]["access_roads"]["miles"] > 0

    reviewed_rating = stats["osm_paths"]["reviewed_streets"]["rating"]
    assert reviewed_rating == 1
    assert all(f["properties"]["lts"] == reviewed_rating for f in reviewed)
    assert all(f["properties"]["fac"] == 0 for f in reviewed)
    assert all(f["properties"]["kind"] == 0 for f in reviewed)
    reviewed_names = {f["properties"].get("nm") for f in reviewed}
    assert {"Commonwealth Drive", "University Court"} <= reviewed_names
    assert stats["osm_paths"]["reviewed_streets"]["segments"] == len(reviewed)
    assert stats["osm_paths"]["reviewed_streets"]["miles"] > 0
    campus_stats = stats["osm_paths"]["campus_walkways"]
    assert campus_stats["campus_relation_id"] == 4815526
    assert campus_stats["scope"] == "academic core"
    assert all(
        street in campus_stats["boundary_source"]
        for street in (
            "Rose Street",
            "Washington Avenue",
            "S Limestone Street",
            "Avenue of Champions",
        )
    )
    assert campus_stats["rating"] == 1
    assert campus_stats["segments"] == len(campus)
    assert campus_stats["miles"] > 0
    parallel = [f for f in campus if f["properties"].get("cb") == 1]
    assert 0 < len(parallel) < len(campus), (
        "road preference must apply only to campus paths near bike facilities"
    )
    assert campus_stats["parallel_bike_segments"] == len(parallel)
    assert campus_stats["parallel_bike_miles"] > 0
    assert all(f["properties"]["lts"] == 1 for f in campus)
    assert all(f["properties"]["fac"] == 6 for f in campus)
    assert all("u" in f["properties"] and "v" in f["properties"] for f in campus), (
        "every published UK campus walkway must participate in routing"
    )
    core = Polygon(load_params()["osm.academic_core_polygon"]).buffer(0.00002)
    assert all(core.covers(shape(f["geometry"])) for f in campus), (
        "generic UK walkways must remain inside the academic core"
    )


def test_baptist_health_cut_through_is_routable(manifest, features):
    access = [
        f for f in features
        if f["properties"].get("src") == "osm"
        and f["properties"].get("nm") == "Baptist Health Entrance 1"
    ]
    if not access:
        pytest.skip("OSM access-road supplement disabled for this build")

    graph = load(manifest["files"]["graph"])
    graph_ids = {edge[2] for edge in graph["edges"]}
    assert all(f["id"] in graph_ids for f in access)
    assert all("u" in f["properties"] and "v" in f["properties"] for f in access)

    degree = {}
    access_ids = {
        f["id"] for f in features
        if f["properties"].get("src") == "osm"
        and f["properties"].get("osm_role") == "access"
    }
    access_adj = {}
    access_edge_by_id = {}
    for u, v, *_ in graph["edges"]:
        degree[u] = degree.get(u, 0) + 1
        degree[v] = degree.get(v, 0) + 1
    for edge in graph["edges"]:
        u, v, edge_id, miles, *_ = edge
        if edge_id not in access_ids:
            continue
        access_edge_by_id[edge_id] = edge
        access_adj.setdefault(u, []).append((v, edge_id))
        access_adj.setdefault(v, []).append((u, edge_id))

    seed = access_edge_by_id[access[0]["id"]]
    seen_nodes = {seed[0], seed[1]}
    seen_edges = set()
    frontier = list(seen_nodes)
    while frontier:
        node = frontier.pop()
        for neighbour, edge_id in access_adj.get(node, []):
            seen_edges.add(edge_id)
            if neighbour not in seen_nodes:
                seen_nodes.add(neighbour)
                frontier.append(neighbour)

    corridor_miles = sum(access_edge_by_id[i][3] for i in seen_edges)
    exits = [
        node for node in seen_nodes
        if degree.get(node, 0) > len(access_adj.get(node, []))
    ]
    assert corridor_miles >= 0.15
    assert len(exits) >= 2, "the Baptist corridor must join the wider graph twice"

    # The west/south end is physically about 9 m from Hiltonia Park. This was
    # once discarded by a 120 m attachment-thinning window, making the browser
    # route all the way around the hospital despite a visible short connection.
    hiltonia_nodes = {
        node
        for f in features
        if f["properties"].get("nm") == "Hiltonia Park"
        for node in (f["properties"].get("u"), f["properties"].get("v"))
        if node is not None
    }
    all_access_nodes = set(access_adj)
    adjacency = {}
    for u, v, _edge_id, miles, _lts, *_ in graph["edges"]:
        adjacency.setdefault(u, []).append((v, miles))
        adjacency.setdefault(v, []).append((u, miles))

    distance = {node: 0.0 for node in all_access_nodes}
    heap = [(0.0, node) for node in all_access_nodes]
    heapq.heapify(heap)
    nearest = None
    while heap:
        miles, node = heapq.heappop(heap)
        if miles != distance[node]:
            continue
        if node in hiltonia_nodes:
            nearest = miles
            break
        for neighbour, edge_miles in adjacency.get(node, []):
            candidate = miles + edge_miles
            if candidate < distance.get(neighbour, float("inf")):
                distance[neighbour] = candidate
                heapq.heappush(heap, (candidate, neighbour))

    assert nearest is not None
    assert nearest * 1609.344 <= 15, (
        f"Baptist access is {nearest * 1609.344:.1f} m by graph from "
        "Hiltonia Park despite a roughly 9 m physical gap"
    )


def test_commonwealth_drive_is_lts1_and_joins_the_graph(manifest, features):
    reviewed = [
        f for f in features
        if f["properties"].get("osm_role") == "reviewed_street"
    ]
    commonwealth = [
        f for f in reviewed
        if f["properties"].get("nm") == "Commonwealth Drive"
    ]
    if not commonwealth:
        pytest.skip("reviewed OSM street supplement disabled for this build")

    assert all(f["properties"]["lts"] == 1 for f in commonwealth)
    assert sum(f["properties"]["mi"] for f in commonwealth) >= 0.5

    graph = load(manifest["files"]["graph"])
    reviewed_ids = {f["id"] for f in reviewed}
    commonwealth_ids = {f["id"] for f in commonwealth}
    reviewed_adj = {}
    all_adj = {}
    reviewed_graph_ids = set()
    for u, v, edge_id, *_ in graph["edges"]:
        all_adj.setdefault(u, []).append((v, edge_id))
        all_adj.setdefault(v, []).append((u, edge_id))
        if edge_id in reviewed_ids:
            reviewed_graph_ids.add(edge_id)
            reviewed_adj.setdefault(u, []).append((v, edge_id))
            reviewed_adj.setdefault(v, []).append((u, edge_id))

    assert commonwealth_ids <= reviewed_graph_ids
    seed = next(iter(reviewed_adj))
    seen_nodes = {seed}
    seen_edges = set()
    frontier = [seed]
    while frontier:
        node = frontier.pop()
        for neighbour, edge_id in reviewed_adj.get(node, []):
            seen_edges.add(edge_id)
            if neighbour not in seen_nodes:
                seen_nodes.add(neighbour)
                frontier.append(neighbour)

    assert commonwealth_ids <= seen_edges, (
        "all Commonwealth Drive pieces must form one continuous corridor"
    )
    exits = {
        node for node in seen_nodes
        if any(edge_id not in reviewed_ids for _, edge_id in all_adj.get(node, []))
    }
    assert len(exits) >= 2, (
        "Commonwealth Drive must join the wider routing graph at both ends"
    )


def test_no_property_is_ever_null(features):
    """detail.js branches on key presence to distinguish 'not measured' from a
    value. A null would render as a blank cell -- the bug this replaces."""
    for f in features[:4000]:
        for key, value in f["properties"].items():
            assert value is not None, f"feature {f['id']} has null {key}"


def test_lts_values_match_the_frontend_palette(features):
    """js/config.js defines LTS 0-4 only. A 5 would render with no colour."""
    assert {f["properties"]["lts"] for f in features} <= {0, 1, 2, 3, 4}


def test_fac_codes_match_the_frontend_table(features):
    assert {f["properties"].get("fac", 0) for f in features} <= set(range(8))


def test_every_lfucg_street_rating_replays_from_its_exported_inputs(features):
    """No block may have a different LTS from otherwise identical inputs.

    This replays the classifier over every published LFUCG street, using the
    exact facility, road class, inferred lanes, speed and traffic values shipped
    to the browser. It catches stale ratings and hidden per-layer drift across
    the entire map rather than relying on a few hand-selected corridors.
    """
    from lexbike import export, lts, params

    rules = lts.Ruleset.from_params(params.load())
    facility_by_code = {code: name for name, code in export.FAC_CODES.items()}
    failures = []

    for feature in features:
        props = feature["properties"]
        if props.get("kind") != 0 or props.get("src") == "osm":
            continue
        expected = lts.lts_for_segment(
            facility_by_code[props["fac"]],
            props["rc"],
            props["ln"],
            props.get("sp"),
            props.get("ad"),
            rules,
        )
        if props["lts"] != expected:
            failures.append((feature["id"], props["lts"], expected))

    assert not failures, f"published ratings disagree with their inputs: {failures[:10]}"


def test_routing_graph_and_drawn_segments_use_the_same_lts(manifest, features):
    """The route colour/penalty and the line visible underneath must agree."""
    by_id = {feature["id"]: feature["properties"]["lts"] for feature in features}
    graph = load(manifest["files"]["graph"])
    failures = [
        (edge_id, graph_lts, by_id.get(edge_id))
        for _u, _v, edge_id, _miles, graph_lts, *_ in graph["edges"]
        if by_id.get(edge_id) != graph_lts
    ]
    assert not failures, f"graph and map LTS disagree: {failures[:10]}"


def test_beaumont_facility_covers_both_one_way_carriageways(features):
    """LFUCG supplies one named facility line for both directions of the loop."""
    beaumont = [
        feature["properties"]
        for feature in features
        if feature["properties"].get("kind") == 0
        and feature["properties"].get("nm") == "Beaumont Centre Cir"
    ]

    assert len(beaumont) >= 20, "expected both carriageways of the loop"
    assert all(props["fac"] == 4 for props in beaumont)
    assert all(props["lts"] == 2 for props in beaumont)


def test_coordinates_are_rounded_for_payload_size(features):
    for f in features[:500]:
        for x, y in f["geometry"]["coordinates"]:
            assert round(x, 5) == x and round(y, 5) == y


# --------------------------------------------------------------------------
#  layer split — js/main.js restoreSelection() infers the source from this
# --------------------------------------------------------------------------

def test_the_three_layers_partition_the_network(manifest):
    """js/main.js restoreSelection() infers a feature's source from its own
    properties. If the split stops being a clean partition, deep links resolve
    against the wrong source and selection silently does nothing."""
    net = load(manifest["files"]["network"])["features"]
    ctx = load(manifest["files"]["context"])["features"]
    res = load(manifest["files"]["residential"])["features"]

    assert all(f["properties"].get("fac") for f in net), "network = has a facility"
    # "not low-stress" is not the same as lts > 2: LTS 0 (bikes prohibited) is
    # excluded from the low-stress network too, so it belongs in context.
    assert all(not f["properties"].get("fac") and f["properties"]["lts"] not in (1, 2)
               for f in ctx), "context = no facility, not low-stress"
    assert all(not f["properties"].get("fac") and f["properties"]["lts"] in (1, 2)
               for f in res), "residential = no facility, low-stress"
    assert {f["properties"]["lts"] for f in ctx} == {0, 3, 4}

    ids = {f["id"] for f in net} | {f["id"] for f in ctx} | {f["id"] for f in res}
    assert len(ids) == len(net) + len(ctx) + len(res), "layers must be disjoint"


def test_layer_stats_let_the_legend_report_what_is_drawn(manifest):
    """The legend needs per-layer mileage when a user hides neighbourhood streets."""
    s = load(manifest["files"]["stats"])
    for name in ("network", "context", "residential"):
        assert name in s["layers"], f"js/views/legend.js reads stats.layers.{name}"
        assert s["layers"][name]["by_lts"], f"stats.layers.{name}.by_lts"

    # Per-layer LTS mileage must reconcile with the citywide totals, or the
    # legend's "20 mi of 701" would not add up.
    citywide = {r["lts"]: r["miles"] for r in s["by_lts"]}
    summed = {}
    for layer in s["layers"].values():
        for lts, mi in layer["by_lts"].items():
            summed[int(lts)] = summed.get(int(lts), 0) + mi
    for lts, total in citywide.items():
        assert abs(summed.get(lts, 0) - total) < 0.5, f"LTS {lts} mileage disagrees"


def test_sensitivity_sweep_sees_every_exported_feature(manifest, features):
    """The sweep reads exported GeoJSON back to find segments whose LTS moves.

    It must read every layer. When the export went from two layers to three, a
    hardcoded file list dropped 3,996 context features and, worse, treated
    segments that moved between files as absent rather than changed — the sweep
    reported 315 changed segments instead of ~2,700, and the confidence field it
    feeds silently overstated certainty.
    """
    from lexbike.pipeline import _lts_by_id

    seen = _lts_by_id(DATA)
    assert len(seen) == len(features), (
        f"sweep sees {len(seen)} of {len(features)} features; "
        "a layer is missing from its file list"
    )


def test_critical_path_stays_within_budget(manifest):
    """The whole point of the rebuild. Guarded here so a schema change cannot
    quietly reintroduce a multi-megabyte first paint."""
    import gzip

    import tomllib

    with open("params.toml", "rb") as fh:
        params = tomllib.load(fh)
    # Read what the build actually did, not what params.toml says: `--set
    # osm.enabled=true` never touches the file, so trusting the file here made
    # this test fail against a perfectly valid OSM build.
    stats = load(manifest["files"]["stats"])
    budget = params["export"]["budget_kb"]["network.geojson"]
    if stats.get("osm_paths", {}).get("enabled"):
        budget = params["export"]["budget_kb_osm"]["network.geojson"]

    raw = (DATA / manifest["files"]["network"]).read_bytes()
    kb = len(gzip.compress(raw, 6)) / 1024
    assert kb < budget, f"critical path is {kb:.0f} KB gzipped against {budget}"

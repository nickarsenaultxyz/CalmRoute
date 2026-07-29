"""The frontend's assumptions about the exported data, asserted in CI-able form.

The JavaScript reads specific property names off specific files. Nothing in
Python enforces that, so a rename in export.py would break the map silently and
only be noticed by opening it. These tests close that gap.

They skip when data/ has not been built, so a fresh clone can still run the
suite.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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
                "graph", "islands", "gaps", "planned"):
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


# --------------------------------------------------------------------------
#  stats — js/views/legend.js reads these exact paths
# --------------------------------------------------------------------------

def test_stats_paths_the_legend_reads(manifest):
    s = load(manifest["files"]["stats"])
    assert all("lts" in r and "miles" in r for r in s["by_lts"])
    for key in ("miles", "islands", "largest_island_share_pct"):
        assert s["low_stress"][key] is not None
    assert s["ridable_lts3"]["miles"] is not None
    # detail.js dates the traffic figure from this; an undated count would
    # imply it is current, and these span 2001-2024.
    assert s["data_sources"]["aadt_count_years"]["median"]


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
    for key in ("lts", "fac", "kind", "nm", "sp", "ad", "mi", "cf", "basis", "isl"):
        assert key in present, f"the UI reads properties.{key}"


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
    """Quiet streets are hidden by default and carry most of the low-stress
    mileage, so a legend row showing the citywide total beside a nearly empty
    map would be misleading. The legend needs per-layer, per-LTS mileage."""
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

    raw = (DATA / manifest["files"]["network"]).read_bytes()
    kb = len(gzip.compress(raw, 6)) / 1024
    assert kb < 90, f"critical path is {kb:.0f} KB gzipped"

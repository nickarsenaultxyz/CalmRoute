"""Export contract.

These pin the properties the frontend reads. Changing any of them is a
breaking change to the map, so each one is asserted explicitly.
"""

from __future__ import annotations

import json

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString

from lexbike import export
from lexbike import params as params_mod


@pytest.fixture(scope="module")
def params():
    return params_mod.load()


def row(**kw):
    base = {
        "id": 1, "lts": 2, "fac": "lane", "kind": "street",
        "geometry": LineString([(-84.5, 38.04), (-84.499, 38.041)]),
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
#  Property emission
# ---------------------------------------------------------------------------

def test_unknown_properties_are_omitted_not_nulled(params):
    """The current map renders an empty cell for a missing AADT, which reads as
    'zero traffic' rather than 'not measured'. Omitting the key forces the UI to
    branch on presence."""
    f = export._feature(pd.Series(row(aadt=float("nan"), speed_mph=None)), 5)
    assert "ad" not in f["properties"]
    assert "sp" not in f["properties"]
    assert f["properties"]["lts"] == 2


def test_known_properties_are_emitted_with_short_keys(params):
    f = export._feature(
        pd.Series(row(aadt=4800.0, speed_mph=25.0, lanes=2, rdclass=6, mi=0.34)), 5
    )
    p = f["properties"]
    assert p["ad"] == 4800 and p["sp"] == 25 and p["ln"] == 2 and p["rc"] == 6
    assert p["mi"] == pytest.approx(0.34)


def test_feature_id_is_top_level_for_maplibre_feature_state(params):
    """MapLibre's promoteId/feature-state hover needs a top-level id."""
    f = export._feature(pd.Series(row(id=98765)), 5)
    assert f["id"] == 98765


def test_facility_and_kind_are_integer_codes(params):
    f = export._feature(pd.Series(row(fac="path", kind="facility")), 5)
    assert f["properties"]["fac"] == export.FAC_CODES["path"]
    assert f["properties"]["kind"] == export.KIND_CODES["facility"]


def test_island_omitted_when_high_stress(params):
    assert "isl" not in export._feature(pd.Series(row(island=-1)), 5)["properties"]
    assert export._feature(pd.Series(row(island=7)), 5)["properties"]["isl"] == 7


def test_coordinates_are_rounded_to_contract_precision(params):
    r = row(geometry=LineString([(-84.5123456, 38.0412345), (-84.4987654, 38.0498765)]))
    # __geo_interface__ yields tuples; JSON serializes them as arrays either way.
    coords = [list(c) for c in export._feature(pd.Series(r), 5)["geometry"]["coordinates"]]
    assert coords[0] == [-84.51235, 38.04123]
    for x, y in coords:
        assert len(str(x).split(".")[1]) <= 5


def test_rounding_preserves_shared_endpoints(params):
    """Topology must survive export: two segments meeting at one coordinate must
    still meet after rounding, or the browser sees a break the graph denies."""
    shared = (-84.5001239, 38.0400001)
    a = export._feature(
        pd.Series(row(id=1, geometry=LineString([(-84.51, 38.04), shared]))), 5)
    b = export._feature(
        pd.Series(row(id=2, geometry=LineString([shared, (-84.49, 38.04)]))), 5)
    assert list(a["geometry"]["coordinates"][-1]) == list(b["geometry"]["coordinates"][0])


# ---------------------------------------------------------------------------
#  Presentation
# ---------------------------------------------------------------------------

def test_street_names_are_title_cased():
    """The source shouts ('TRENT CIR'); a public map should not."""
    assert export._title_case("TRENT CIR") == "Trent Cir"
    assert export._title_case("W SECOND ST") == "W Second St"
    assert export._title_case("MAN O' WAR BLVD").startswith("Man")


def test_title_case_keeps_directional_suffixes_but_not_street_types():
    assert export._title_case("BROADWAY NW") == "Broadway NW"
    assert export._title_case("NEW CIRCLE RD NE") == "New Circle Rd NE"
    # 'ST' is a street type, not a direction -- a generic two-letter rule breaks it
    assert export._title_case("MAIN ST") == "Main St"


# ---------------------------------------------------------------------------
#  Provenance
# ---------------------------------------------------------------------------

def _frame(rows):
    return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")


def test_measured_volume_yields_high_confidence(params):
    from lexbike import io

    gdf = _frame([row(aadt_src=io.AADT_STATION, rdclass=6, fac="none")])
    out = export.add_provenance(gdf, params)
    assert out["cf"].iloc[0] == export.CONF_HIGH
    assert out["basis"].iloc[0] == export.BASIS_TYPE_SPEED_TRAFFIC


def test_coarsely_imputed_volume_yields_low_confidence(params):
    from lexbike import io

    gdf = _frame([row(aadt_src=io.AADT_IMPUTED_COARSE, rdclass=5, fac="none")])
    out = export.add_provenance(gdf, params)
    assert out["cf"].iloc[0] == export.CONF_LOW
    assert out["basis"].iloc[0] == export.BASIS_TYPE_SPEED


def test_offstream_facility_is_high_confidence_without_any_count(params):
    """A trail's rating does not depend on traffic at all, so a missing count is
    not a weakness there."""
    from lexbike import io

    gdf = _frame([row(aadt_src=io.AADT_IMPUTED_COARSE, rdclass=6, fac="path")])
    out = export.add_provenance(gdf, params)
    assert out["cf"].iloc[0] == export.CONF_HIGH
    assert out["basis"].iloc[0] == export.BASIS_TYPE


# ---------------------------------------------------------------------------
#  Methodology
# ---------------------------------------------------------------------------

def test_methodology_carries_every_code_the_features_use(params):
    m = export.build_methodology(params)
    for code in export.FAC_CODES.values():
        assert str(code) in m["codes"]["fac"]
    for code in export.KIND_CODES.values():
        assert str(code) in m["codes"]["kind"]
    for level in range(5):
        assert str(level) in m["codes"]["lts"]


def test_methodology_digest_matches_the_executed_ruleset(params):
    """Published rules cannot drift from the rules that produced the numbers."""
    m = export.build_methodology(params)
    assert m["params_digest"] == params.digest
    assert m["rules"]["lts"]["mixed"]["speed_35_lts"] == params["lts.mixed.speed_35_lts"]


def test_methodology_is_json_serializable(params):
    json.dumps(export.build_methodology(params), default=str)

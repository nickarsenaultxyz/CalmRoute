"""Council district lookup.

The email action sends real constituent mail to a real elected official, so
the two things that must hold are: the right district, and no message at all
rather than a confidently wrong recipient.
"""

from __future__ import annotations

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString, Polygon

from lexbike import council
from lexbike import params as params_mod

LON0, LAT0 = -84.50, 38.04


@pytest.fixture(scope="module")
def params():
    return params_mod.load()


def districts_fixture():
    """Two adjacent square districts, split at LON0."""
    west = Polygon([(LON0 - 0.1, LAT0 - 0.1), (LON0, LAT0 - 0.1),
                    (LON0, LAT0 + 0.1), (LON0 - 0.1, LAT0 + 0.1)])
    east = Polygon([(LON0, LAT0 - 0.1), (LON0 + 0.1, LAT0 - 0.1),
                    (LON0 + 0.1, LAT0 + 0.1), (LON0, LAT0 + 0.1)])
    return gpd.GeoDataFrame(
        {
            "DISTRICT": [1, 2],
            "REP": ["Ada West", "Bo East"],
            "EMAIL": ["awest@example.gov", "beast@example.gov"],
            "TELEPHONE": ["(859) 555-0001", "(859) 555-0002"],
            "URL": ["https://example.gov/1", "https://example.gov/2"],
            "geometry": [west, east],
        },
        crs="EPSG:4326",
    )


def edges_fixture():
    return gpd.GeoDataFrame(
        {
            "id": [1, 2, 3],
            "geometry": [
                LineString([(LON0 - 0.05, LAT0), (LON0 - 0.04, LAT0)]),   # west
                LineString([(LON0 + 0.04, LAT0), (LON0 + 0.05, LAT0)]),   # east
                LineString([(LON0 + 5.0, LAT0), (LON0 + 5.01, LAT0)]),    # outside
            ],
        },
        crs="EPSG:4326",
    )


# --------------------------------------------------------------------------

def test_segments_get_the_district_that_contains_them(params):
    out = council.attach(edges_fixture(), districts_fixture(), params)
    assert out.loc[out["id"] == 1, "council"].iloc[0] == 1
    assert out.loc[out["id"] == 2, "council"].iloc[0] == 2


def test_segments_outside_every_district_get_none(params):
    """Better no recipient than a confidently wrong one."""
    out = council.attach(edges_fixture(), districts_fixture(), params)
    assert pd.isna(out.loc[out["id"] == 3, "council"].iloc[0])


def test_result_stays_aligned_with_the_input(params):
    """A spatial join can emit extra rows where polygons share an edge; the
    council column must still line up row-for-row with the segments."""
    edges = edges_fixture()
    out = council.attach(edges, districts_fixture(), params)
    assert len(out) == len(edges)
    assert list(out["id"]) == list(edges["id"])


def test_roster_carries_what_the_email_needs(params):
    r = council.roster(districts_fixture())
    assert set(r) == {"1", "2"}
    assert r["1"]["name"] == "Ada West"
    assert r["1"]["email"] == "awest@example.gov"
    assert r["2"]["url"].startswith("https://")


def test_roster_omits_blank_fields():
    d = districts_fixture()
    d.loc[0, "EMAIL"] = None
    r = council.roster(d)
    assert "email" not in r["1"], "a blank must be absent, not an empty string"
    assert r["1"]["name"] == "Ada West"


# --------------------------------------------------------------------------
#  Degradation — CI fetches this layer live, so failure must not break a build
# --------------------------------------------------------------------------

def test_missing_layer_leaves_segments_unassigned(params):
    edges = edges_fixture()
    out = council.attach(edges, None, params)
    assert len(out) == len(edges)
    assert out["council"].isna().all()


def test_missing_layer_yields_an_empty_roster():
    assert council.roster(None) == {}


def test_fetch_failure_returns_none_rather_than_raising(params, tmp_path, monkeypatch):
    """A council-server outage must degrade the map, not fail the deploy."""
    monkeypatch.setattr(council, "CACHE", tmp_path / "absent.geojson")
    bad = params_mod.load(overrides=[
        'council.source_url="https://127.0.0.1:9/nope.geojson"',
        "council.fetch_timeout_s=1",
    ])
    assert council.fetch(bad, use_cache=False) is None


def test_unreadable_cache_returns_none(tmp_path, monkeypatch):
    junk = tmp_path / "junk.geojson"
    junk.write_text("this is not geojson")
    assert council._load(junk) is None


def test_layer_without_a_district_column_is_rejected(tmp_path):
    """Guards against silently attaching meaningless numbers if LFUCG renames
    the field."""
    gpd.GeoDataFrame(
        {"NAME": ["x"], "geometry": [Polygon([(0, 0), (1, 0), (1, 1)])]},
        crs="EPSG:4326",
    ).to_file(tmp_path / "no_district.geojson", driver="GeoJSON")
    assert council._load(tmp_path / "no_district.geojson") is None

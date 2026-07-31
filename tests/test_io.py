"""Source-ingestion contracts for live LFUCG refreshes."""

from __future__ import annotations

import geopandas as gpd
from shapely.geometry import LineString

from lexbike import io
from lexbike import params as params_mod


def test_current_lfucg_bike_schema_and_blank_status_are_normalized(tmp_path):
    """The public view uses lowercase fields and has explicit-name status gaps."""
    source = tmp_path / "bike.geojson"
    gpd.GeoDataFrame(
        [
            {
                "objectid": 42,
                "type_facility": "Shared Use Path",
                "status": None,
                "type_road": "Off Road",
                "alttype_facility": "Shared Use Path",
                "name_facility": "EXISTING SHARED USE TRAIL",
                "name_network": "TEST",
                "geometry": LineString([(-84.5, 38.0), (-84.49, 38.01)]),
            }
        ],
        crs="EPSG:4326",
    ).to_file(source, driver="GeoJSON")

    result = io.load_bike_facilities(params_mod.load(), source)

    assert result.loc[0, "id_src"] == 42
    assert result.loc[0, "fac"] == "path"
    assert result.loc[0, "status"] == "Existing"
    assert result.loc[0, "network_name"] == "TEST"


def test_old_and_live_lfucg_on_road_names_are_normalized(tmp_path):
    """Accept the road-name field before and after LFUCG's semantic swap."""
    source = tmp_path / "bike.geojson"
    gpd.GeoDataFrame(
        [
            {
                "objectid": 207,
                "type_facility": "Buffered Bicycle Lane",
                "status": "Existing",
                "type_road": "On Road",
                "alttype_facility": None,
                "name_facility": "Beaumont Centre Cir",
                "name_network": None,
                "geometry": LineString([(-84.56, 38.02), (-84.55, 38.02)]),
            },
            {
                "objectid": 208,
                "type_facility": "Buffered Bicycle Lane",
                "status": "Existing",
                "type_road": "On Road",
                "alttype_facility": None,
                "name_facility": "EXISTING BUFFERED BIKE LANE",
                "name_network": "Beaumont Centre Cir",
                "geometry": LineString([(-84.56, 38.021), (-84.55, 38.021)]),
            },
        ],
        crs="EPSG:4326",
    ).to_file(source, driver="GeoJSON")

    result = io.load_bike_facilities(params_mod.load(), source)

    assert result.loc[0, "network_name"] == "Beaumont Centre Cir"
    assert result.loc[1, "network_name"] == "Beaumont Centre Cir"
    assert set(result["fac"]) == {"buffered"}

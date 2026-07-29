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

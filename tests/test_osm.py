"""Policy tests for the deliberately narrow OSM routing supplement."""

from shapely.geometry import LineString, Polygon

from lexbike.osm import (
    QUERY,
    _overpass_poly,
    _parse,
    _role,
    _seeded_access_indices,
)
from lexbike.params import load


def test_dedicated_cycle_infrastructure_is_a_path():
    assert _role({"highway": "cycleway"}) == "path"
    assert _role({"highway": "footway", "bicycle": "designated"}) == "path"


def test_uk_campus_walking_ways_are_paths_only_in_reviewed_scope():
    for highway in ("footway", "path", "pedestrian"):
        tags = {"highway": highway}
        assert _role(tags) is None
        assert _role(tags, campus_path=True) == "campus_path"


def test_uk_campus_path_exception_respects_explicit_prohibitions():
    assert _role(
        {"highway": "footway", "bicycle": "no"},
        campus_path=True,
    ) is None
    assert _role(
        {"highway": "path", "access": "private"},
        campus_path=True,
    ) is None
    assert _role(
        {"highway": "pedestrian", "access": "no"},
        campus_path=True,
    ) is None


def test_generic_walkways_are_queried_and_clipped_to_the_academic_core():
    params = load()
    polygon_coords = params["osm.academic_core_polygon"]
    encoded = _overpass_poly(polygon_coords)
    first_lon, first_lat = polygon_coords[0]

    assert encoded.split()[:2] == [str(float(first_lat)), str(float(first_lon))]
    assert 'way(poly:"{academic_core_poly}")' in QUERY
    assert "area.uk_campus" not in QUERY

    payload = {
        "elements": [{
            "type": "way",
            "id": 123,
            "nodes": [1, 2],
            "tags": {"highway": "footway"},
            "geometry": [
                {"lon": -84.510, "lat": 38.038},
                {"lon": -84.498, "lat": 38.038},
            ],
        }]
    }
    result = _parse(payload, params)
    geometry = result.iloc[0].geometry
    core = Polygon(polygon_coords)

    assert result.iloc[0].osm_role == "campus_path"
    assert core.buffer(1e-12).covers(geometry)
    assert geometry.length < LineString([
        (-84.510, 38.038), (-84.498, 38.038)
    ]).length


def test_bicycle_designated_paths_remain_available_outside_the_academic_core():
    params = load()
    payload = {
        "elements": [{
            "type": "way",
            "id": 456,
            "nodes": [1, 2],
            "tags": {"highway": "footway", "bicycle": "designated"},
            "geometry": [
                {"lon": -84.550, "lat": 38.050},
                {"lon": -84.549, "lat": 38.050},
            ],
        }]
    }
    result = _parse(payload, params)

    assert result.iloc[0].osm_role == "path"
    assert result.iloc[0].geometry.coords[0] == (-84.550, 38.050)


def test_locally_reviewed_osm_false_positive_is_excluded():
    params = load()
    excluded_id = params["osm.excluded_way_ids"][0]
    payload = {
        "elements": [
            {
                "type": "way",
                "id": excluded_id,
                "nodes": [1, 2],
                "tags": {
                    "highway": "footway",
                    "footway": "sidewalk",
                    "bicycle": "designated",
                    "bridge": "yes",
                },
                "geometry": [
                    {"lon": -84.4660198, "lat": 37.9958647},
                    {"lon": -84.4654002, "lat": 37.9955386},
                ],
            },
            {
                "type": "way",
                "id": excluded_id + 1,
                "nodes": [3, 4],
                "tags": {"highway": "cycleway"},
                "geometry": [
                    {"lon": -84.550, "lat": 38.050},
                    {"lon": -84.549, "lat": 38.050},
                ],
            },
        ]
    }

    result = _parse(payload, params)

    assert excluded_id not in set(result.osm_id)
    assert set(result.osm_id) == {excluded_id + 1}


def test_explicit_two_way_bicycle_service_road_is_access():
    assert _role({
        "highway": "service",
        "bicycle": "yes",
        "name": "Baptist Health Entrance 1",
    }) == "access"
    assert _role({
        "highway": "service",
        "bicycle": "permissive",
        "name": "Campus Connector",
        "service": "driveway",
        "oneway": "no",
    }) == "access"


def test_parking_private_and_one_way_service_roads_are_excluded():
    base = {"highway": "service", "bicycle": "yes", "name": "Campus Connector"}
    assert _role({**base, "service": "parking_aisle"}) is None
    assert _role({**base, "name": "Visitor Parking Road"}) is None
    assert _role({**base, "access": "private"}) is None
    assert _role({**base, "access": "no"}) is None
    assert _role({**base, "oneway": "yes"}) is None
    assert _role({**base, "oneway": "-1"}) is None


def test_generic_service_roads_need_explicit_bicycle_permission():
    assert _role({"highway": "service"}) is None
    assert _role({"highway": "service", "bicycle": "no", "name": "Campus Road"}) is None
    assert _role({"highway": "service", "bicycle": "yes"}) == "access"


def test_only_named_reviewed_streets_receive_the_reviewed_role():
    reviewed = {"Commonwealth Drive", "University Court"}
    assert _role({
        "highway": "residential",
        "maxspeed": "25 mph",
        "name": "Commonwealth Drive",
        "oneway": "yes",
    }, reviewed) == "reviewed_street"
    assert _role({
        "highway": "residential",
        "name": "University Court",
    }, reviewed) == "reviewed_street"
    assert _role({
        "highway": "residential",
        "maxspeed": "25 mph",
        "name": "Unreviewed Drive",
    }, reviewed) is None
    assert _role({
        "highway": "residential",
        "name": "Commonwealth Drive",
        "access": "private",
    }, reviewed) is None


def test_unnamed_access_continuations_are_kept_only_when_connected_to_seed():
    rows = [
        {
            "osm_role": "access",
            "name": "Baptist Health Entrance 1",
            "_nodes": (1, 2),
        },
        {"osm_role": "access", "name": "", "_nodes": (2, 3)},
        {"osm_role": "access", "name": "", "_nodes": (8, 9)},
        {"osm_role": "path", "name": "", "_nodes": (3, 4)},
    ]
    assert _seeded_access_indices(
        rows, {"Baptist Health Entrance 1"}
    ) == {0, 1}

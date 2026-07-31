"""Policy tests for the deliberately narrow OSM routing supplement."""

from lexbike.osm import _role, _seeded_access_indices


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

"""Params loading and override behaviour.

The sensitivity sweep drives every variant through ``--set``, so a silent
failure here would make the whole sweep report "no change" and look reassuring.
"""

from __future__ import annotations

import pytest

from lexbike import params as params_mod


def test_loads_default_params():
    p = params_mod.load()
    assert p["lts.mixed.speed_35_lts"] == 3, "the locked 35 mph decision"
    assert p["lts.low_stress_max"] == 2
    assert p["meta.crs_working"] == 32616


def test_facility_mapping_covers_every_source_value():
    """Guards the raise-on-unknown contract in io.load_bike_facilities."""
    p = params_mod.load()
    mapping = p["facility.type_map"]
    expected = {
        "Shared Use Path",
        "Buffered Bicycle Lane",
        "Bicycle Lane",
        "Shoulder",
        "Sharrow",
        "Preferred Route",
    }
    assert expected <= set(mapping), (
        "every Type_Facility value in Bicycle_Network_Master.geojson"
    )

    ranks = p["facility.rank"]
    for category in mapping.values():
        assert category in ranks, f"{category!r} has no protective rank"


def test_preferred_route_is_not_credited_as_infrastructure():
    """A signed route with no treatment must rate as mixed traffic.

    AltType_Facility is a recommended upgrade, not existing infrastructure:
    the 37 Preferred Route segments whose AltType says 'Bicycle Lane' carry
    Name_Facility == 'EXISTING PREFERRED ROUTE'.
    """
    p = params_mod.load()
    assert p["facility.type_map"]["Preferred Route"] == "none"
    assert p["facility.rank"]["none"] == 0


def test_override_scalar_types():
    p = params_mod.load(overrides=["lts.mixed.speed_35_lts=2"])
    assert p["lts.mixed.speed_35_lts"] == 2

    p = params_mod.load(overrides=["conflation.buffer_m=8.0"])
    assert p["conflation.buffer_m"] == pytest.approx(8.0)

    p = params_mod.load(overrides=["confidence.high_requires_station=false"])
    assert p["confidence.high_requires_station"] is False


def test_override_rejects_unknown_parameter():
    """A typo must fail loudly rather than inventing a parameter nothing reads."""
    with pytest.raises(params_mod.ParamsError, match="no such parameter"):
        params_mod.load(overrides=["lts.mixed.speed_36_lts=2"])


def test_override_rejects_malformed_argument():
    with pytest.raises(params_mod.ParamsError, match="malformed override"):
        params_mod.load(overrides=["lts.mixed.speed_35_lts"])


def test_digest_tracks_effective_ruleset():
    base = params_mod.load()
    same = params_mod.load()
    changed = params_mod.load(overrides=["lts.mixed.speed_35_lts=2"])

    assert base.digest == same.digest, "digest must be stable across loads"
    assert base.digest != changed.digest, "an override must change the digest"
    assert len(base.digest) == 12


def test_tree_is_a_copy():
    p = params_mod.load()
    p.tree["lts"]["low_stress_max"] = 99
    assert p["lts.low_stress_max"] == 2


def test_reviewed_connectors_are_narrowly_scoped():
    p = params_mod.load()
    connectors = p["conflation.reviewed_connectors"]

    assert len(connectors) == 2
    assert {c["target_street"] for c in connectors} == {
        "S MILL ST", "SCOTT ST",
    }
    assert all(len(c["source"]) == 2 for c in connectors)
    assert max(c["max_m"] for c in connectors) <= 90


def test_reviewed_street_closures_are_narrowly_scoped():
    p = params_mod.load()
    closures = p["network.reviewed_street_closures"]

    assert len(closures) == 2
    assert {row["id"] for row in closures} == {8284, 9283}
    assert {row["street_name"] for row in closures} == {
        "PROSPECT AVE", "SIMPSON AVE",
    }
    assert all(row["ordinance"] == "O-063-2014" for row in closures)
    assert all(row["source_url"].startswith("https://") for row in closures)
    assert all(row.get("why") for row in closures)
    simpson = next(row for row in closures if row["id"] == 9283)
    assert simpson["closed_from"] == "start"
    assert simpson["public_piece_id"] == 725000001
    assert 725_000_000 <= simpson["public_piece_id"] < 730_000_000
    assert simpson["max_boundary_m"] <= 0.5


def test_reviewed_street_links_are_narrowly_scoped():
    p = params_mod.load()
    links = p["network.reviewed_street_links"]

    assert len(links) == 4
    assert {tuple(link["street_ids"]) for link in links} == {
        (14350, 8328),
        (15470, 13491),
        (8284, 9283),
        (5687, 60),
    }
    assert len({link["id"] for link in links}) == len(links)
    assert all(730_000_000 <= link["id"] < 740_000_000 for link in links)
    assert all(len(link["geometry"]) >= 2 for link in links)
    assert all(link["max_endpoint_m"] <= 3 for link in links)
    assert all(link["max_length_m"] <= 50 for link in links)
    assert all(link.get("why") for link in links)
    prospect = next(link for link in links if link["id"] == 730000003)
    assert prospect["lts"] == 0
    assert prospect["road_bike_ok"] is False


def test_reviewed_exact_junction_is_narrowly_scoped():
    p = params_mod.load()
    junctions = p["network.reviewed_exact_junctions"]

    assert len(junctions) == 1
    assert junctions[0]["target_street_id"] == 6199
    assert junctions[0]["endpoint_street_id"] == 2023
    assert junctions[0]["point"] == [-84.49861, 38.0478]
    assert junctions[0].get("why")


def test_reviewed_on_road_assignments_are_narrowly_scoped():
    p = params_mod.load()
    assignments = p["conflation.reviewed_on_road_assignments"]

    assert len(assignments) == 8
    expected = {
        14: {14633},
        15: {14633, 14640, 14643},
        106: {13616, 13617},
        149: {5642, 5644},
        167: {13180},
        170: {13119},
        172: {12295},
        173: {13678, 15329, 15330},
    }
    actual = {
        assignment["source_id"]: set(assignment["street_ids"])
        for assignment in assignments
    }
    assert actual == expected
    assert all(assignment["facility"] in {"lane", "buffered"}
               for assignment in assignments)
    assert max(assignment["max_distance_m"] for assignment in assignments) <= 30
    assert all(assignment.get("why") for assignment in assignments)


def test_aadt_model_excludes_underrepresented_local_streets():
    p = params_mod.load()
    assert set(p["aadt.model.eligible_rdclasses"]) == {1, 2, 3, 4, 5}
    assert 0.5 < p["aadt.model.quantile"] < 1
    assert p["aadt.model.min_station_keys"] >= 100


def test_sensitivity_runs_reference_real_parameters():
    """Every sweep variant must be applicable, or the sweep is a no-op."""
    p = params_mod.load()
    runs = p["sensitivity.runs"]
    assert len(runs) >= 6

    seen = set()
    for run in runs:
        assert run["name"] not in seen, f"duplicate sweep name {run['name']!r}"
        seen.add(run["name"])
        assert run.get("why"), f"{run['name']} needs a rationale"
        for dotted, value in run["set"].items():
            overridden = params_mod.load(overrides=[f"{dotted}={value!r}".replace("'", '"')])
            assert overridden[dotted] == value

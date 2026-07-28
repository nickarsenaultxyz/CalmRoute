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
    assert expected <= set(mapping), "every Type_Facility value in lexbike.geojson"

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

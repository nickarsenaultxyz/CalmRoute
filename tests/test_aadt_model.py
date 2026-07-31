"""AADT model quality and domain-safety checks."""

from __future__ import annotations

from lexbike import aadt_model, io
from lexbike.params import load


def test_model_is_used_only_in_the_reviewed_domain():
    params = load()
    streets = io.load_streets(params)
    measured = io.load_aadt(params)
    out = io.attach_aadt(streets, measured, params)

    modelled = out["aadt_src"] == io.AADT_IMPUTED_MODEL
    assert int(modelled.sum()) > 1000
    assert set(out.loc[modelled, "rdclass"]) <= set(
        params["aadt.model.eligible_rdclasses"]
    )
    assert not (
        out.loc[~out["rdclass"].isin(params["aadt.model.eligible_rdclasses"]),
                "aadt_src"]
        == io.AADT_IMPUTED_MODEL
    ).any()


def test_route_grouped_model_beats_the_median_cascade():
    params = load()
    streets = io.load_streets(params)
    measured = io.load_aadt(params)
    station_keys = streets["KYDOT"].astype("string").str.strip()
    targets = station_keys.map(measured)
    routes = station_keys.map(io.aadt_route_groups(params)).fillna(station_keys)

    scores = aadt_model.cross_validate(
        streets, station_keys, targets, routes, params
    )
    baseline = scores["median"]
    model = scores["model"]

    assert model["station_keys"] >= 300
    assert model["rmsle"] <= baseline["rmsle"] * 0.8
    assert model["median_ape_pct"] < baseline["median_ape_pct"]
    assert (
        model["lts_bin_accuracy_pct"]
        >= baseline["lts_bin_accuracy_pct"] + 2
    )

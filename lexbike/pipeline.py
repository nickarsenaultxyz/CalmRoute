"""Build orchestration.

Stage order, and why it is this order:

  1. load + validate sources          (io)
  2. conflate facilities -> centrelines, generate off-road connectors (conflate)
  3. classify every segment           (lts)
  4. build graph, label islands, rank barriers (network)
  5. write artifacts                  (export)

Stage 2 precedes 3 because a segment's facility, speed and volume must all be
resolved onto the one canonical geometry before anything can be rated.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from . import io
from . import lts as lts_mod
from .params import Params

log = logging.getLogger(__name__)


def classify(streets, params: Params):
    """Apply the classifier to every centreline.

    Vectorized where it is cheap (the lane surrogate is a small rule table) and
    row-wise for the LTS decision itself, which is a branch tree over four
    scalars. 13,775 rows is well inside the range where clarity beats cleverness.
    """
    rules = lts_mod.Ruleset.from_params(params)
    gdf = streets.copy()

    gdf["lanes"] = [
        lts_mod.lane_surrogate(
            int(rd),
            bool(dr),
            None if pd.isna(cc) else int(cc),
            rules.lane_rules,
            rules.lane_default,
        )
        for rd, dr, cc in zip(gdf["rdclass"], gdf["directional"], gdf["cartoclass"])
    ]

    gdf["speed_mph"] = [lts_mod.round_to_posted_speed(v) for v in gdf["speed_mph"]]

    if "fac" not in gdf.columns:
        gdf["fac"] = "none"

    gdf["lts"] = [
        lts_mod.lts_for_segment(
            fac,
            int(rd),
            int(ln),
            None if pd.isna(sp) else float(sp),
            None if pd.isna(ad) else float(ad),
            rules,
        )
        for fac, rd, ln, sp, ad in zip(
            gdf["fac"], gdf["rdclass"], gdf["lanes"], gdf["speed_mph"], gdf["aadt"]
        )
    ]
    gdf["lts"] = gdf["lts"].astype("int8")
    return gdf


def summarize(gdf, params: Params) -> None:
    """Log the LTS distribution by mileage.

    Mileage, not segment counts: segment length spans two orders of magnitude
    here, so counts are a misleading public metric.
    """
    rules = lts_mod.Ruleset.from_params(params)
    metres = io.to_working_crs(gdf, params).geometry.length
    miles = metres / 1609.344
    labels = params["lts.labels"]

    log.info("LTS distribution:")
    total = miles.sum()
    for level in sorted(gdf["lts"].unique()):
        hit = gdf["lts"] == level
        label = labels[str(level)]["short"]
        log.info(
            "  LTS %d  %6d seg  %8.1f mi  %5.1f%%  %s",
            level, int(hit.sum()), miles[hit].sum(),
            100 * miles[hit].sum() / total, label,
        )
    low = gdf["lts"].map(lambda v: lts_mod.is_low_stress(v, rules))
    log.info(
        "  total  %6d seg  %8.1f mi   | low-stress (LTS 1-%d): %d seg, %.1f mi",
        len(gdf), total, rules.low_stress_max, int(low.sum()), miles[low].sum(),
    )


def run_build(params: Params, out_dir: Path, *, skip_size_check: bool = False) -> dict:
    """Run the full pipeline, writing artifacts into ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)

    log.info("--- stage 1/5: load sources ---")
    streets = io.load_streets(params)
    facilities = io.load_bike_facilities(params)
    aadt_by_station = io.load_aadt(params)

    log.info(
        "sources ok: %d centrelines, %d facilities (%d on-road / %d off-road), %d AADT stations",
        len(streets),
        len(facilities),
        int(facilities["on_road"].sum()),
        int((~facilities["on_road"]).sum()),
        len(aadt_by_station),
    )

    streets = io.attach_aadt(streets, aadt_by_station, params)

    # Stage 2 (conflation) is not wired yet, so every segment is currently rated
    # as mixed traffic. That is the correct no-facility baseline and lets the
    # classifier be checked against all 13,775 real segments before conflation
    # lands; facilities can only lower these ratings, never raise them.
    log.info("--- stage 3/5: classify (no-facility baseline) ---")
    rated = classify(streets, params)
    summarize(rated, params)

    raise NotImplementedError(
        "stage 2 (conflation) — see task #3. Sources load, AADT resolves, and the "
        "classifier runs over the full network."
    )


def run_sensitivity(params: Params, out_dir: Path, doc_path: Path) -> None:
    raise NotImplementedError("sensitivity sweep — see task #6")

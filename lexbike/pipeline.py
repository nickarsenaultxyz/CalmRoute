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

from . import io
from .params import Params

log = logging.getLogger(__name__)


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

    raise NotImplementedError(
        "stage 2 (conflation) — see task #3. Sources load and validate cleanly."
    )


def run_sensitivity(params: Params, out_dir: Path, doc_path: Path) -> None:
    raise NotImplementedError("sensitivity sweep — see task #6")

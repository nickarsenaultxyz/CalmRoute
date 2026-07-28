"""Artifact writers — the contract the MapLibre frontend depends on.

Short property keys, keys omitted rather than null, coordinates rounded to
``meta.coord_decimals``. Rounding is topology-safe: identical input coordinates
round identically, so the exact endpoint coincidence the graph relies on
survives.

Not yet implemented — see task #5.
"""

from __future__ import annotations

from pathlib import Path


def print_stats(out_dir: Path) -> None:
    raise NotImplementedError("stats printing — see task #5")

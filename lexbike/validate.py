"""Post-build validation.

Two checks with different jobs:

**Golden corridors** pin the rating of specific real segments that a person has
looked at and judged. They catch a rule change that is wrong in a way the
aggregates hide.

**Aggregate stability** pins the headline figures within a tolerance. It catches
a change that is wrong everywhere at once — the failure mode golden corridors
miss, because a handful of hand-picked segments can all still be right while the
other 14,000 shift.

Neither replaces the other, and neither replaces the sensitivity sweep, which
asks a different question: not "did this change?" but "how much would it change
if a judgement call went the other way?"
"""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path

from .params import Params

log = logging.getLogger(__name__)

GOLDEN_PATH = Path("tests/golden_corridors.csv")
BASELINE_PATH = Path("tests/baseline_stats.json")


def _load_exported_lts(out_dir: Path) -> dict[int, dict]:
    features: dict[int, dict] = {}
    for name in ("network.geojson", "residential.geojson"):
        path = out_dir / name
        if not path.exists():
            continue
        for feat in json.loads(path.read_text())["features"]:
            features[int(feat["id"])] = feat["properties"]
    return features


def check_golden(out_dir: Path) -> bool:
    """Compare hand-labelled corridors against the build."""
    if not GOLDEN_PATH.exists():
        log.warning(
            "no golden corridors at %s — skipping. This file is the only check "
            "that anchors ratings to human judgement about real streets; the "
            "aggregate check below cannot substitute for it.", GOLDEN_PATH,
        )
        return True

    exported = _load_exported_lts(out_dir)
    rows = list(csv.DictReader(GOLDEN_PATH.open()))
    labelled = [r for r in rows if (r.get("expected_lts") or "").strip()]

    if not labelled:
        log.warning(
            "%s has %d rows but none carry an expected_lts — the file is a "
            "template awaiting labels, so nothing is being checked",
            GOLDEN_PATH, len(rows),
        )
        return True

    failures = []
    missing = []
    for r in labelled:
        sclink = int(r["sclink"])
        expected = int(r["expected_lts"])
        props = exported.get(sclink)
        if props is None:
            missing.append(r)
            continue
        got = int(props["lts"])
        if got != expected:
            failures.append((r, got))

    if missing:
        log.warning(
            "%d golden corridor(s) are not in the build output; a split may have "
            "moved their id: %s",
            len(missing), ", ".join(m["road_name"] for m in missing[:5]),
        )

    log.info("golden corridors: %d checked, %d failed", len(labelled), len(failures))
    if failures:
        log.error("  %-10s %-28s %8s %8s  %s", "sclink", "road", "expected", "got", "rationale")
        for r, got in failures:
            log.error(
                "  %-10s %-28s %8s %8d  %s",
                r["sclink"], r["road_name"][:28], r["expected_lts"], got,
                r.get("rationale", ""),
            )
    return not failures


def check_aggregates(out_dir: Path) -> bool:
    """Compare headline figures against the committed baseline."""
    stats_path = out_dir / "stats.json"
    if not stats_path.exists():
        log.error("no stats at %s; run `make build` first", stats_path)
        return False
    stats = json.loads(stats_path.read_text())

    if not BASELINE_PATH.exists():
        BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
        BASELINE_PATH.write_text(json.dumps(_baseline_from(stats), indent=2) + "\n")
        log.warning(
            "no baseline existed; wrote the current build to %s. Review it before "
            "committing — it now defines what counts as a regression.", BASELINE_PATH,
        )
        return True

    baseline = json.loads(BASELINE_PATH.read_text())
    current = _baseline_from(stats)
    ok = True

    log.info("aggregate stability:")
    for key, tolerance in [
        ("total_miles", 0.02),
        ("low_stress_miles", 0.05),
        ("ridable_lts3_miles", 0.05),
        ("islands", 0.10),
        ("largest_island_miles", 0.10),
    ]:
        want, got = baseline["values"].get(key), current["values"][key]
        if want in (None, 0):
            continue
        drift = (got - want) / want
        flag = "" if abs(drift) <= tolerance else "  DRIFT"
        log.info("  %-22s %10.1f -> %10.1f  %+6.1f%% (tol %.0f%%)%s",
                 key, want, got, 100 * drift, 100 * tolerance, flag)
        if abs(drift) > tolerance:
            ok = False

    if not ok:
        log.error(
            "headline figures moved beyond tolerance. If the change is intended, "
            "update %s and say why in the commit message.", BASELINE_PATH,
        )
    return ok


def _baseline_from(stats: dict) -> dict:
    low = stats["low_stress"]
    return {
        "ruleset_version": stats["ruleset_version"],
        "params_digest": stats["params_digest"],
        "note": "Regenerate deliberately, never automatically: this file is what "
                "makes an unintended rule change visible.",
        "values": {
            "total_miles": stats["total_miles"],
            "low_stress_miles": low["miles"],
            "ridable_lts3_miles": stats["ridable_lts3"]["miles"],
            "islands": low["islands"],
            "largest_island_miles": low["largest_island_miles"],
        },
    }


def run_validate(params: Params, out_dir: Path) -> bool:
    golden_ok = check_golden(out_dir)
    aggregates_ok = check_aggregates(out_dir)
    if golden_ok and aggregates_ok:
        log.info("validation passed")
    return golden_ok and aggregates_ok

"""Artifact writers — the contract the MapLibre frontend depends on.

Design rules, and the reasons for them:

**Short keys, integer codes.** Code-to-label lookups ship once in
``methodology.json`` rather than repeating "Comfortable for most adults" 7,700
times.

**Keys are omitted when unknown, never emitted as null.** This is smaller, and
more importantly it forces the UI to branch on presence rather than render the
empty cell that is the current map's bug — AADT is a real measurement for only
15% of segments.

**Coordinates are rounded to ``meta.coord_decimals``.** Rounding is
topology-safe: identical input coordinates round identically, so the exact
endpoint coincidence the graph depends on survives. The graph nodes are built at
this same precision for that reason.

**Sizes are asserted against a budget.** The previous map shipped 904 KB
gzipped with every byte blocking first paint. A schema regression should fail
the build, not the user's phone.
"""

from __future__ import annotations

import gzip
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

from . import io
from . import lts as lts_mod
from .params import Params

log = logging.getLogger(__name__)

METRES_PER_MILE = 1609.344

#: Integer codes for compact properties. Labels live in methodology.json.
FAC_CODES = {
    "none": 0, "sharrow": 1, "shoulder": 2, "lane": 3,
    "buffered": 4, "protected": 5, "path": 6, "connector": 7,
}
KIND_CODES = {"street": 0, "facility": 1, "connector": 2}

#: How much of the rating rests on measurement rather than inference.
BASIS_TYPE = 0            # facility alone decides (path, connector)
BASIS_TYPE_SPEED = 1      # speed known, volume imputed
BASIS_TYPE_SPEED_TRAFFIC = 2  # volume is a real count

CONF_LOW, CONF_MEDIUM, CONF_HIGH = 0, 1, 2


class ExportError(Exception):
    """An artifact violates the contract."""


# ---------------------------------------------------------------------------
#  Derived per-segment fields
# ---------------------------------------------------------------------------

def load_flipped_ids(path: Path) -> set[int]:
    """Segment ids whose LTS changes under some sensitivity variant.

    Produced by ``lexbike sensitivity``. Absent on a first build, which is why
    this returns an empty set rather than failing — but the confidence figures
    then overstate certainty until the sweep has been run.
    """
    if not path.exists():
        log.info(
            "no sensitivity results at %s; confidence will not include the "
            "LTS-flip demotion (run `make sensitivity`)", path,
        )
        return set()
    return {int(x) for x in json.loads(path.read_text())}


def add_provenance(
    edges: gpd.GeoDataFrame, params: Params, flipped: set[int] | None = None
) -> gpd.GeoDataFrame:
    """Add ``basis`` and ``cf`` (confidence).

    These exist so the map can be honest about a real weakness: KYTC only counts
    state-maintained routes, so 85% of segments carry an imputed volume drawn
    from the busiest third of their class. Surfacing that per segment is the
    difference between a map a planner will engage with and one they dismiss.

    The sensitivity sweep later demotes any segment whose LTS flips under a
    parameter variant — see :mod:`lexbike.validate`.
    """
    out = edges.copy()

    src = out["aadt_src"].fillna(io.AADT_IMPUTED_COARSE).astype("int8")
    off_stream = out["fac"].isin(["path", "protected", "connector"])

    basis = np.where(
        off_stream, BASIS_TYPE,
        np.where(src == io.AADT_STATION, BASIS_TYPE_SPEED_TRAFFIC, BASIS_TYPE_SPEED),
    )
    out["basis"] = basis.astype("int8")

    # A quiet local street rated from its posted speed is genuinely well
    # understood even without a traffic count; a collector resting on an imputed
    # volume is not.
    local = out["rdclass"] == 6
    has_facility = out["fac"] != "none"

    conf = np.full(len(out), CONF_LOW, dtype="int8")
    conf = np.where(src == io.AADT_IMPUTED_NARROW, CONF_MEDIUM, conf)
    conf = np.where(
        off_stream | ((src == io.AADT_STATION) & (has_facility | local)),
        CONF_HIGH, conf,
    )
    # A rating that flips under a defensible parameter change is uncertain by
    # definition, whatever its data provenance. This is the mechanical version
    # of that claim rather than an asserted one.
    if flipped and bool(params["confidence.sensitivity_flip_forces_low"]):
        demote = out["id"].isin(flipped).to_numpy()
        n_demoted = int((conf[demote] > CONF_LOW).sum())
        conf = np.where(demote, CONF_LOW, conf)
        log.info(
            "confidence: %d segments demoted to low because their LTS flips "
            "under a sensitivity variant", n_demoted,
        )

    out["cf"] = conf.astype("int8")
    return out


# ---------------------------------------------------------------------------
#  GeoJSON writing
# ---------------------------------------------------------------------------

def _round_coords(geom, decimals: int):
    """Round every coordinate. Identical inputs round identically, so shared
    endpoints stay shared and the graph's topology survives export."""
    def rd(seq):
        return [(round(x, decimals), round(y, decimals)) for x, y in seq]

    from shapely.geometry import LineString, MultiLineString

    if geom.geom_type == "LineString":
        return LineString(rd(geom.coords))
    if geom.geom_type == "MultiLineString":
        return MultiLineString([rd(p.coords) for p in geom.geoms])
    return geom


def _feature(row, decimals: int) -> dict:
    """One GeoJSON feature with unknown properties omitted rather than nulled."""
    props: dict = {"lts": int(row["lts"]), "fac": FAC_CODES[row["fac"]]}

    if row.get("kind") is not None:
        props["kind"] = KIND_CODES.get(row["kind"], 0)
    for key, col, cast in [
        ("mi", "mi", float), ("u", "u", int), ("v", "v", int),
        ("sp", "speed_mph", int), ("ad", "aadt", int), ("ln", "lanes", int),
        ("rc", "rdclass", int), ("basis", "basis", int), ("cf", "cf", int),
        ("cd", "council", int),
    ]:
        val = row.get(col)
        if val is None or (isinstance(val, float) and np.isnan(val)) or pd.isna(val):
            continue
        props[key] = round(cast(val), 4) if cast is float else cast(val)

    name = row.get("road_name")
    if name is not None and not pd.isna(name) and str(name).strip():
        props["nm"] = _title_case(str(name))

    isl = row.get("island")
    if isl is not None and not pd.isna(isl) and int(isl) >= 0:
        props["isl"] = int(isl)

    # Omit the common LFUCG value and mark only supplemental OSM paths. This
    # preserves auditable provenance without repeating a string on every road.
    if row.get("source") == "osm":
        props["src"] = "osm"
        role = row.get("osm_role")
        if role is not None and not pd.isna(role):
            props["osm_role"] = str(role)

    reviewed = row.get("connector_reviewed")
    if reviewed is not None and not pd.isna(reviewed) and bool(reviewed):
        props["rv"] = 1

    return {
        "type": "Feature",
        "id": int(row["id"]),
        "properties": props,
        "geometry": _round_coords(row["geometry"], decimals).__geo_interface__,
    }


#: Kept uppercase. Deliberately an explicit set: a "two-letter uppercase word"
#: rule also matches ST, the commonest street type in the source, and turns
#: 'W SECOND ST' into 'W Second ST'.
_KEEP_UPPER = {"NW", "NE", "SW", "SE", "US", "KY", "I"}
_KEEP_LOWER = {"of", "the", "and", "at", "on"}


def _title_case(name: str) -> str:
    """'TRENT CIR' -> 'Trent Cir'. The source shouts; the map should not."""
    parts = []
    for i, word in enumerate(name.strip().split()):
        if word.upper() in _KEEP_UPPER:
            parts.append(word.upper())
        elif word.lower() in _KEEP_LOWER and i > 0:
            parts.append(word.lower())
        else:
            parts.append(word.capitalize())
    return " ".join(parts)


def write_geojson(
    gdf: gpd.GeoDataFrame, path: Path, params: Params, *, simplify: float
) -> None:
    decimals = int(params["meta.coord_decimals"])
    frame = gdf.copy()
    if simplify:
        frame["geometry"] = frame.geometry.simplify(simplify, preserve_topology=True)

    features = [_feature(row, decimals) for _, row in frame.iterrows()]
    payload = {"type": "FeatureCollection", "features": features}
    path.write_text(json.dumps(payload, separators=(",", ":")))


def write_json(obj, path: Path) -> None:
    path.write_text(json.dumps(obj, separators=(",", ":"), default=str))


# ---------------------------------------------------------------------------
#  Size budget
# ---------------------------------------------------------------------------

def check_sizes(out_dir: Path, params: Params, *, enforce: bool = True) -> dict:
    """Compare gzipped artifact sizes against the budget.

    Gzipped, because GitHub Pages serves these compressed (verified) and that is
    what the user actually downloads. Raw size would over-report by ~4x and lead
    to optimizing the wrong thing.
    """
    budgets = dict(params["export.budget_kb"])
    if params.get("osm.enabled", False):
        # Supplementary paths legitimately enlarge two artifacts; the defaults
        # stay tight so a regression in the normal build is still caught.
        budgets.update(params.get("export.budget_kb_osm", {}))
    report = {}
    problems = []

    for name, budget in budgets.items():
        path = out_dir / name
        if not path.exists():
            continue
        raw_kb = path.stat().st_size / 1024
        gz_kb = len(gzip.compress(path.read_bytes(), 6)) / 1024
        report[name] = {"raw_kb": round(raw_kb, 1), "gzip_kb": round(gz_kb, 1),
                        "budget_kb": budget}
        flag = "" if gz_kb <= budget else "  OVER BUDGET"
        log.info("  %-22s %8.1f KB raw  %7.1f KB gzip  (budget %d)%s",
                 name, raw_kb, gz_kb, budget, flag)
        if gz_kb > budget:
            problems.append(f"{name} is {gz_kb:.1f} KB gzipped against a {budget} KB budget")

    total_gz = sum(v["gzip_kb"] for v in report.values())
    critical = report.get("network.geojson", {}).get("gzip_kb", 0)
    log.info("  total %.1f KB gzipped; critical path %.1f KB "
             "(the map it replaces shipped 904 KB, all of it blocking)",
             total_gz, critical)

    if problems and enforce:
        raise ExportError("; ".join(problems))
    return report


# ---------------------------------------------------------------------------
#  Stats and methodology
# ---------------------------------------------------------------------------

def build_stats(edges, islands, barriers, params: Params, extra: dict) -> dict:
    """Headline figures for the page.

    Mileage everywhere, not segment counts: segment length spans two orders of
    magnitude, so counts would be a misleading public metric.

    Connectors are excluded from every mileage figure. They are synthetic links
    joining a trail to the street beside it -- 1,483 of them, median 49 ft --
    and counting them as ridable network would have added 12.8 miles of
    infrastructure nobody built to the "Relaxed" total.
    """
    rules = lts_mod.Ruleset.from_params(params)
    real = edges[edges["fac"] != "connector"] if "fac" in edges.columns else edges
    connector_mi = (
        io.to_working_crs(edges[edges["fac"] == "connector"], params)
        .geometry.length.sum() / METRES_PER_MILE
        if "fac" in edges.columns else 0.0
    )
    edges = real
    miles = io.to_working_crs(edges, params).geometry.length / METRES_PER_MILE
    labels = params["lts.labels"]

    by_lts = []
    for level in sorted(edges["lts"].unique()):
        hit = edges["lts"] == level
        by_lts.append({
            "lts": int(level),
            "label": labels[str(level)]["short"],
            "detail": labels[str(level)]["detail"],
            "segments": int(hit.sum()),
            "miles": round(float(miles[hit].sum()), 1),
        })

    low = edges["lts"].map(lambda v: lts_mod.is_low_stress(v, rules))
    low_miles = float(miles[low].sum())
    ridable = edges["lts"].between(1, 3)
    major = islands[islands["major"]] if len(islands) else islands

    conf_share = {}
    if "cf" in edges.columns:
        for level, name in [(CONF_HIGH, "high"), (CONF_MEDIUM, "medium"), (CONF_LOW, "low")]:
            sel = low & (edges["cf"] == level)
            conf_share[name] = round(100 * float(miles[sel].sum()) / low_miles, 1) if low_miles else 0.0

    largest = float(major["miles"].iloc[0]) if len(major) else 0.0
    osm_source = (
        edges["source"].fillna("").eq("osm")
        if "source" in edges.columns
        else pd.Series(False, index=edges.index)
    )
    osm_role = (
        edges["osm_role"].fillna("")
        if "osm_role" in edges.columns
        else pd.Series("", index=edges.index)
    )
    osm_path = osm_source & osm_role.eq("path")
    osm_access = osm_source & osm_role.eq("access")
    osm_reviewed_street = osm_source & osm_role.eq("reviewed_street")

    return {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ruleset_version": params["meta.ruleset_version"],
        "params_digest": params.digest,
        "total_miles": round(float(miles.sum()), 1),
        "connector_miles_excluded": round(float(connector_mi), 1),
        "by_lts": by_lts,
        "low_stress": {
            "max_lts": rules.low_stress_max,
            "miles": round(low_miles, 1),
            "segments": int(low.sum()),
            "islands": int(len(islands)),
            "major_islands": int(len(major)),
            "largest_island_miles": round(largest, 1),
            "largest_island_share_pct": round(100 * largest / low_miles, 1) if low_miles else 0.0,
            "confidence_share_pct": conf_share,
        },
        # The pairing that survives the whole sensitivity sweep. The island count
        # alone is a methodology choice, not a measurement, and moves by a factor
        # of three under defensible-sounding threshold changes.
        "ridable_lts3": {
            "miles": round(float(miles[ridable].sum()), 1),
            "note": "LTS 3 and below - comfortable for confident riders",
        },
        "barriers": {
            "projects": int(len(barriers)),
            "distinct_island_pairs": int(barriers["best_for_pair"].sum()) if len(barriers) else 0,
        },
        "data_sources": {
            "street_centrelines": (
                "LFUCG + reviewed OpenStreetMap street exceptions"
                if params.get("osm.enabled", False) else "LFUCG"
            ),
            "bike_facilities": (
                "LFUCG + OpenStreetMap supplementary paths"
                if params.get("osm.enabled", False) else "LFUCG"
            ),
            "traffic_counts": "KYTC",
            "aadt_count_years": extra.get("aadt_years", {}),
            "aadt_measured_segments": extra.get("aadt_measured", 0),
            "aadt_measured_pct": extra.get("aadt_measured_pct", 0.0),
        },
        "osm_paths": {
            "enabled": bool(params.get("osm.enabled", False)),
            "attribution": params.get("osm.attribution", ""),
            "paths": {
                "segments": int(osm_path.sum()),
                "miles": round(float(miles[osm_path].sum()), 1),
            },
            "access_roads": {
                "segments": int(osm_access.sum()),
                "miles": round(float(miles[osm_access].sum()), 1),
                "rating": int(params.get("osm.access_lts", 2)),
            },
            "reviewed_streets": {
                "segments": int(osm_reviewed_street.sum()),
                "miles": round(float(miles[osm_reviewed_street].sum()), 1),
                "rating": int(params.get("osm.reviewed_street_lts", 1)),
                "required_names": params.get(
                    "osm.required_reviewed_street_names", []
                ),
            },
        },
        "coverage": {
            "sampled_areas": params.get("coverage.sampled_areas"),
            "osm_named_streets": params.get("coverage.osm_named_streets"),
            "missing_from_lfucg": params.get("coverage.missing_from_lfucg"),
            "missing_pct": round(
                100 * params["coverage.missing_from_lfucg"]
                / params["coverage.osm_named_streets"], 1)
            if params.get("coverage.osm_named_streets") else None,
        },
        "limitations": [
            params["coverage.note"],
            "Traffic counts exist only on state-maintained routes, so volumes for "
            "most local streets are imputed from class medians and are estimates.",
            "Lane counts are not in any source; they are inferred from road class, "
            "one-way status and cartographic class.",
            "On-street parking and bike lane width are not modelled - neither is "
            "recorded in the available data.",
            "Ratings describe built infrastructure only. Planned and funded "
            "projects are shown separately.",
        ],
    }


def build_methodology(params: Params) -> dict:
    """The executed ruleset, serialized.

    Generated from the same params object the build ran with, so the published
    methodology cannot drift from the rules that produced the numbers.
    """
    return {
        "ruleset_version": params["meta.ruleset_version"],
        "params_digest": params.digest,
        "codes": {
            "fac": {str(v): k for k, v in FAC_CODES.items()},
            "kind": {str(v): k for k, v in KIND_CODES.items()},
            "basis": {
                str(BASIS_TYPE): "Rated from facility type alone",
                str(BASIS_TYPE_SPEED): "Rated from street type and posted speed; "
                                       "no traffic count is available",
                str(BASIS_TYPE_SPEED_TRAFFIC): "Rated from street type, posted speed "
                                               "and a measured traffic count",
            },
            "cf": {str(CONF_LOW): "low", str(CONF_MEDIUM): "medium", str(CONF_HIGH): "high"},
            "lts": params["lts.labels"],
            "rdclass": params["rdclass.labels"],
        },
        "rules": params.tree,
    }


def print_stats(out_dir: Path) -> None:
    path = out_dir / "stats.json"
    if not path.exists():
        raise ExportError(f"no stats at {path}; run `make build` first")
    stats = json.loads(path.read_text())

    print(f"\nLexington bike stress - ruleset {stats['ruleset_version']} "
          f"(digest {stats['params_digest']})")
    print(f"generated {stats['generated']}\n")
    print(f"{'':4} {'segments':>9} {'miles':>8}   label")
    for row in stats["by_lts"]:
        print(f"LTS{row['lts']} {row['segments']:>9} {row['miles']:>8.1f}   {row['label']}")

    low = stats["low_stress"]
    print(f"\ntotal {stats['total_miles']:.1f} mi")
    print(f"low-stress (LTS 1-{low['max_lts']}): {low['miles']:.1f} mi in "
          f"{low['islands']} islands; largest holds {low['largest_island_miles']:.1f} mi "
          f"({low['largest_island_share_pct']:.1f}%)")
    print(f"ridable at LTS 3 and below: {stats['ridable_lts3']['miles']:.1f} mi")
    print(f"confidence of low-stress mileage: {low['confidence_share_pct']}")
    print(f"barriers: {stats['barriers']['projects']} projects across "
          f"{stats['barriers']['distinct_island_pairs']} distinct island pairs")

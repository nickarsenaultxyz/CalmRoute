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

import geopandas as gpd
import numpy as np
import pandas as pd

from . import conflate, council, export, io, network, osm as osm_mod
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

    log.info("--- stage 2/5: conflate facilities onto centrelines ---")
    # Ratings must reflect what is built, so planned/funded projects are excluded
    # here and carried separately for the scenario feature. The old pipeline
    # conflated the two and inflated its ratings with unbuilt infrastructure.
    existing = facilities[facilities["status"] == params["scenario.existing_status"]]
    funded = facilities[facilities["status"] == params["scenario.funded_status"]]
    log.info("rating against %d existing facilities (%d funded held back)",
             len(existing), len(funded))

    streets = conflate.conflate_on_road(streets, existing, params)

    # Supplementary OSM paths, if enabled. They join the same off-road pipeline
    # rather than a parallel one, so they get the same connectors, the same
    # splitting and the same rating rule.
    off_road_input = existing
    osm_paths = osm_mod.fetch(params)
    if osm_paths is not None:
        osm_paths = osm_mod.dedupe(osm_paths, existing, params)
        osm_paths = osm_mod.as_facilities(osm_paths, params)
        osm_mod.quality_gate(osm_paths, params)
        off_road_input = pd.concat([existing, osm_paths], ignore_index=True)
        off_road_input = gpd.GeoDataFrame(
            off_road_input, geometry="geometry", crs=existing.crs)

    paths, connectors = conflate.build_off_road(streets, off_road_input, params)

    # Split centrelines where trail connectors attach, before classification, so
    # both pieces inherit the parent's speed, volume and facility.
    streets = network.split_for_connectors(streets, connectors, params)

    log.info("--- stage 3/5: classify ---")
    rated = classify(streets, params)

    log.info("--- stage 4/5: graph, islands, barriers ---")
    edges = assemble_edges(rated, paths, connectors, params)
    summarize(edges, params)

    graph, node_ids, pairs = network.build_graph(edges, params)
    edges, islands = network.label_islands(edges, pairs, params)
    barriers = network.rank_barriers(edges, pairs, islands, params)

    edges["u"] = [p[0] if p else None for p in pairs]
    edges["v"] = [p[1] if p else None for p in pairs]
    edges["mi"] = io.to_working_crs(edges, params).geometry.length / 1609.344
    # Sensitivity results, when present, demote segments whose LTS is not robust.
    flipped = export.load_flipped_ids(Path('data/_sensitivity/flipped_ids.json'))
    edges = export.add_provenance(edges, params, flipped)

    # Which council member represents a street depends on where it is, so the
    # district is resolved per segment rather than left to the reader.
    districts = council.fetch(params)
    edges = council.attach(edges, districts, params)

    log.info("--- stage 5/5: write artifacts ---")
    stats = write_artifacts(
        edges, islands, barriers, node_ids, funded, streets, params, out_dir,
        skip_size_check=skip_size_check, council_roster=council.roster(districts),
    )
    log.info("wrote %d artifacts to %s/", len(list(out_dir.glob('*'))), out_dir)
    return stats


def write_artifacts(
    edges, islands, barriers, node_ids, funded, streets, params: Params,
    out_dir: Path, *, skip_size_check: bool = False, council_roster: dict | None = None,
) -> dict:
    """Write every file the frontend fetches."""
    rules = lts_mod.Ruleset.from_params(params)

    # Three disjoint layers, split by the role each plays in answering "can I
    # ride here", because that is also the order they are needed in:
    #
    #   network      built bike facilities and trails -- the actual answer, and
    #                the only thing on the critical path
    #   context      busy and prohibited roads with no facility -- the skeleton
    #                that makes the map legible, fetched right after
    #   residential  quiet streets with no facility -- 8,900 features that are
    #                visual mush at the city-wide default zoom, so they wait for
    #                an idle callback or a zoom past 13
    #
    # An earlier split put context in the critical path; measured, it was 170 KB
    # of the 245 KB priority layer, for data that is background.
    is_low = edges["lts"].map(lambda v: lts_mod.is_low_stress(v, rules))
    has_fac = edges["fac"] != "none"

    network = edges[has_fac].copy()
    context = edges[~has_fac & ~is_low].copy()
    residential = edges[~has_fac & is_low].copy()

    assert len(network) + len(context) + len(residential) == len(edges), \
        "layer split must partition the network"
    log.info(
        "layer split: %d facilities / %d context / %d residential",
        len(network), len(context), len(residential),
    )

    export.write_geojson(network, out_dir / "network.geojson", params,
                         simplify=float(params["export.simplify_priority"]))
    export.write_geojson(context, out_dir / "context.geojson", params,
                         simplify=float(params["export.simplify_residential"]))
    export.write_geojson(residential, out_dir / "residential.geojson", params,
                         simplify=float(params["export.simplify_residential"]))

    # Routing graph. Node coordinates plus u/v/length/lts per edge is everything
    # a client-side Dijkstra needs; at ~10.8k nodes it is a few hundred KB and
    # routes in single-digit milliseconds, so nothing is precomputed.
    coords = [None] * len(node_ids)
    decimals = int(params["meta.coord_decimals"])
    for (x, y), idx in node_ids.items():
        coords[idx] = [round(x, decimals), round(y, decimals)]
    routable = edges[edges["u"].notna()]
    export.write_json(
        {
            "nodes": coords,
            "edges": [
                [int(u), int(v), int(i), round(float(m), 4), int(l)]
                for u, v, i, m, l in zip(
                    routable["u"], routable["v"], routable["id"],
                    routable["mi"], routable["lts"],
                )
            ],
            "edge_fields": ["u", "v", "id", "miles", "lts"],
        },
        out_dir / "graph.json",
    )

    export.write_json(
        islands.to_dict(orient="records") if len(islands) else [],
        out_dir / "islands.json",
    )

    if len(barriers):
        export.write_geojson(
            barriers.assign(
                id=range(len(barriers)),
                lts=barriers["current_lts"],
                fac="none",
                road_name=barriers["name"],
            ),
            out_dir / "gaps.geojson", params, simplify=0.0,
        )
        export.write_json(
            barriers.drop(columns="geometry").to_dict(orient="records"),
            out_dir / "gaps.json",
        )

    export.write_json(
        {
            "districts": council_roster or {},
            "fallback": {
                "url": params["council.fallback_url"],
                "label": params["council.fallback_label"],
            },
            # Republished from the city's own layer on every build; see
            # lexbike/council.py for why it is not stored in the repository.
            "source": params["council.source_url"],
        },
        out_dir / "council.json",
    )

    planned = build_planned(funded, streets, edges, params)
    export.write_json(planned, out_dir / "planned.geojson")

    stats = export.build_stats(edges, islands, barriers, params, extra={
        "aadt_years": io.aadt_years(),
        "aadt_measured": int((edges["aadt_src"] == io.AADT_STATION).sum()),
        "aadt_measured_pct": round(
            100 * float((edges["aadt_src"] == io.AADT_STATION).mean()), 1),
    })
    # Per-layer totals, so the legend can label the residential toggle with a
    # real figure instead of deriving one in JavaScript and risking drift.
    metres = io.to_working_crs(edges, params).geometry.length
    # Same reason as build_stats: a synthetic link is not ridable network.
    not_conn = edges["fac"] != "connector"
    stats["layers"] = {
        name: {
            "features": int(not_conn[frame.index].sum()),
            "miles": round(
                float(metres[frame.index[not_conn[frame.index]]].sum()) / 1609.344, 1),
            # Per-LTS too, so the legend can report the mileage actually on
            # screen. With the residential layer off by default, a legend row
            # reading "701 mi" beside a handful of visible blue lines is worse
            # than no number at all.
            "by_lts": {
                str(int(level)): round(float(metres[frame.index[
                    (frame["lts"] == level) & (frame["fac"] != "connector")
                ]].sum()) / 1609.344, 1)
                for level in sorted(frame["lts"].unique())
            },
        }
        for name, frame in
        (("network", network), ("context", context), ("residential", residential))
    }
    stats["planned_projects"] = len(planned.get("projects", []))
    export.write_json(stats, out_dir / "stats.json")
    export.write_json(export.build_methodology(params), out_dir / "methodology.json")

    bounds = edges.total_bounds
    export.write_json(
        {
            "version": stats["generated"][:10],
            "generated": stats["generated"],
            "params_digest": params.digest,
            "bbox": [round(float(b), 5) for b in bounds],
            "center": [round(float((bounds[0] + bounds[2]) / 2), 5),
                       round(float((bounds[1] + bounds[3]) / 2), 5)],
            "zoom": 11.6,
            "low_stress_max": rules.low_stress_max,
            "files": {
                "network": "network.geojson",
                "context": "context.geojson",
                "residential": "residential.geojson",
                "graph": "graph.json",
                "islands": "islands.json",
                "gaps": "gaps.json",
                "gapsGeometry": "gaps.geojson",
                "planned": "planned.geojson",
                "council": "council.json",
                "stats": "stats.json",
                "methodology": "methodology.json",
            },
            "counts": {
                "network": len(network),
                "context": len(context),
                "residential": len(residential),
                "nodes": len(node_ids),
                "islands": len(islands),
                "gaps": len(barriers),
            },
        },
        out_dir / "manifest.json",
    )

    log.info("artifact sizes:")
    export.check_sizes(out_dir, params, enforce=not skip_size_check)
    return stats


def _first_text(*values) -> str:
    """First non-null, non-blank value as a string.

    A plain ``a or b`` chain raises on pandas NA ("boolean value of NA is
    ambiguous"), which is easy to write and only fails on the rows that happen
    to be missing.
    """
    for v in values:
        if v is None or pd.isna(v):
            continue
        text = str(v).strip()
        if text:
            return text
    return ""


def build_planned(funded, streets, edges, params: Params) -> dict:
    """What each funded project would change, for the scenario toggle.

    Only the small part is precomputed: which segment ids a project upgrades and
    to what LTS. The client re-derives the island partition with a union-find on
    every toggle, because 26 projects are 2^26 combinations and precomputing
    outcomes is impossible — while a union-find over 14k edges takes under 5 ms.
    """
    if funded is None or funded.empty:
        return {"projects": [], "new_edges": []}

    rules = lts_mod.Ruleset.from_params(params)
    on_road = funded[funded["on_road"] & (funded["fac"] != "none")]
    projects = []

    if len(on_road):
        credited = conflate.conflate_on_road(
            streets, on_road, params, check_quality=False
        )
        affected = credited[credited["fac"] != "none"]
        by_id = edges.set_index("id")
        for _, row in affected.iterrows():
            if row["id"] not in by_id.index:
                continue
            current = by_id.loc[row["id"]]
            new_lts = lts_mod.lts_for_segment(
                row["fac"], int(current["rdclass"]), int(current["lanes"]),
                None if pd.isna(current["speed_mph"]) else float(current["speed_mph"]),
                None if pd.isna(current["aadt"]) else float(current["aadt"]),
                rules,
            )
            if new_lts < int(current["lts"]):
                projects.append({
                    "id": int(row["id"]),
                    "nm": str(row.get("road_name") or ""),
                    "fac": export.FAC_CODES[row["fac"]],
                    "lts_now": int(current["lts"]),
                    "lts_if_built": int(new_lts),
                })

    # Funded off-road trails are the larger half of the programme (22 of 26) and
    # the part that actually merges islands: they are NEW geometry, not an
    # upgrade to an existing centreline. Each is emitted as an edge joining the
    # existing graph nodes nearest its two ends.
    #
    # Limitation, stated because it bounds what the scenario toggle can claim: a
    # trail end is attached only if an existing node lies within
    # conflation.connector_max_m. A funded trail landing mid-block gets no
    # attachment there and will read as its own island rather than merging one.
    new_edges = []
    off_road = funded[~funded["on_road"]]
    if len(off_road) and len(edges):
        max_m = float(params["conflation.connector_max_m"])
        decimals = int(params["meta.coord_decimals"])

        node_xy: dict[tuple, int] = {}
        for u, v, geom in zip(edges["u"], edges["v"], edges.geometry):
            if pd.isna(u) or pd.isna(v):
                continue
            coords = list(geom.coords) if geom.geom_type == "LineString" else None
            if not coords:
                continue
            node_xy[(round(coords[0][0], decimals), round(coords[0][1], decimals))] = int(u)
            node_xy[(round(coords[-1][0], decimals), round(coords[-1][1], decimals))] = int(v)

        keys = np.array(list(node_xy.keys())) if node_xy else np.empty((0, 2))
        ids = list(node_xy.values())
        off_m = io.to_working_crs(off_road, params)
        # Degrees are fine for choosing the nearest candidate; the accept/reject
        # test below is done in metres.
        for (_, row), geom_m in zip(off_road.iterrows(), off_m.geometry):
            geom = row.geometry
            if geom.geom_type != "LineString" or len(keys) == 0:
                continue
            ends = [geom.coords[0], geom.coords[-1]]
            attached = []
            for x, y in ends:
                d = np.hypot(keys[:, 0] - x, keys[:, 1] - y)
                k = int(np.argmin(d))
                # ~111 km per degree of latitude; adequate for a radius test.
                if d[k] * 111_000 <= max_m:
                    attached.append(ids[k])
                else:
                    attached.append(None)
            new_edges.append({
                "nm": _first_text(row.get("network_name"), row.get("facility_name")),
                "fac": export.FAC_CODES[row["fac"]],
                "lts": rules.path_lts,
                "miles": round(float(geom_m.length) / 1609.344, 4),
                "u": attached[0],
                "v": attached[1],
                "connects_both_ends": attached[0] is not None and attached[1] is not None,
            })

    joined = sum(1 for e in new_edges if e["connects_both_ends"])
    log.info(
        "planned: %d funded facilities -> %d segment upgrades, %d new trail edges "
        "(%d attach at both ends and can merge islands)",
        len(funded), len(projects), len(new_edges), joined,
    )
    return {
        "projects": projects,
        "new_edges": new_edges,
        "funded_facilities": int(len(funded)),
    }


def assemble_edges(streets, paths, connectors, params: Params):
    """Concatenate the three geometry sources into one network frame.

    Off-road paths and connectors are the only geometry outside the centreline
    layer. Everything downstream — graph, islands, barriers, export — reads this
    single frame, so there is one definition of "the network".
    """
    rules = lts_mod.Ruleset.from_params(params)

    frames = [streets.assign(kind="street")]

    if len(paths):
        p = paths.copy()
        p["kind"] = "facility"
        # A path is off the traffic stream: no speed, no volume, no road class.
        p["rdclass"] = 6
        p["lanes"] = 1
        p["speed_mph"] = pd.NA
        p["aadt"] = pd.NA
        p["aadt_src"] = io.AADT_IMPUTED_COARSE
        p["road_name"] = p.get("network_name", pd.Series(dtype="string"))
        p["lts"] = rules.path_lts if "path" in rules.facility_rank else 1
        p["fac"] = "path"
        frames.append(p)

    if len(connectors):
        c = connectors.copy()
        c["kind"] = "connector"
        c["rdclass"] = 6
        c["lanes"] = 1
        c["speed_mph"] = pd.NA
        c["aadt"] = pd.NA
        c["aadt_src"] = io.AADT_IMPUTED_COARSE
        c["road_name"] = pd.NA
        c["lts"] = rules.connector_lts
        c["fac"] = "connector"
        frames.append(c)

    keep = [
        "id", "geometry", "kind", "fac", "lts", "rdclass", "lanes",
        "speed_mph", "aadt", "aadt_src", "road_name",
    ]
    out = pd.concat(
        [f.reindex(columns=[c for c in keep if c in f.columns or c == "geometry"])
         for f in frames],
        ignore_index=True,
    )
    out = gpd.GeoDataFrame(out, geometry="geometry", crs=streets.crs)
    out["lts"] = out["lts"].astype("int8")

    if not out["id"].is_unique:
        dupes = out["id"][out["id"].duplicated()].unique()[:5]
        raise ValueError(
            f"assembled network has duplicate ids (e.g. {list(dupes)}); "
            "feature ids must be unique for map selection and deep links"
        )

    log.info(
        "network assembled: %d features (%d street, %d path, %d connector)",
        len(out),
        int((out["kind"] == "street").sum()),
        int((out["kind"] == "facility").sum()),
        int((out["kind"] == "connector").sum()),
    )
    return out


def run_sensitivity(params: Params, out_dir: Path, doc_path: Path) -> None:
    """Re-run the build once per declared variant and tabulate what moved.

    This is the answer to "why should I believe this map". Several thresholds
    are judgement calls rather than measurements — the 35 mph rating alone moves
    the largest island from 12% to 42% — and publishing the sweep is more
    honest than publishing one number and hoping nobody asks.

    Also feeds the confidence field: a segment whose LTS flips under any variant
    is demoted to low confidence, which is a mechanical definition of
    "uncertain" rather than an asserted one.
    """
    from . import params as params_mod

    out_dir.mkdir(parents=True, exist_ok=True)
    runs = params["sensitivity.runs"]

    log.info("baseline build")
    base = run_build(params, out_dir / "_base", skip_size_check=True)
    base_lts = _lts_by_id(out_dir / "_base")

    rows = [_sweep_row("baseline", "the published ruleset", base, base_lts, base_lts)]
    rows[0].pop("_flipped_ids", None)
    flipped_any: set[int] = set()
    failures: list[dict] = []

    for run in runs:
        name = run["name"]
        overrides = [f"{k}={_toml_literal(v)}" for k, v in run["set"].items()]
        log.info("variant %s (%s)", name, ", ".join(overrides))
        try:
            variant_params = params_mod.load(params.source, overrides)
            stats = run_build(variant_params, out_dir / name, skip_size_check=True)
        except Exception as exc:
            # Recorded, not skipped. A variant that cannot build is itself a
            # result -- it says the parameter is bounded by a quality gate --
            # and dropping it from the table would misrepresent the sweep as
            # having covered ground it did not.
            log.error("  variant %s failed: %s: %s", name, type(exc).__name__, exc)
            failures.append({"variant": name, "why": run.get("why", ""),
                             "error": f"{type(exc).__name__}: {exc}"})
            continue
        variant_lts = _lts_by_id(out_dir / name)
        row = _sweep_row(name, run.get("why", ""), stats, base_lts, variant_lts)
        flipped_any |= row.pop("_flipped_ids")
        rows.append(row)

    _write_sensitivity_doc(rows, failures, flipped_any, doc_path, params)
    export.write_json(sorted(flipped_any), out_dir / "flipped_ids.json")

    log.info(
        "sensitivity: %d variants; %d segments flip LTS under at least one "
        "-> written to %s",
        len(rows) - 1, len(flipped_any), doc_path,
    )


def _toml_literal(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return f'"{value}"'
    return str(value)


def _lts_by_id(out_dir: Path) -> dict[int, int]:
    """Read back the exported LTS per feature id, across EVERY layer.

    Driven by the manifest rather than a hardcoded file list. That is not
    fussiness: when the export went from two layers to three, a hardcoded list
    silently dropped the 3,996 context features from the sweep. It also
    under-counted flips generally, because a segment changing from LTS 3 to 2
    *moves between files* — it looked absent from the baseline rather than
    changed, so it was skipped. The sweep reported 315 changed segments instead
    of ~2,700, and the confidence field it feeds overstated certainty.
    """
    import json

    manifest_path = out_dir / "manifest.json"
    if manifest_path.exists():
        files = json.loads(manifest_path.read_text())["files"]
        names = [v for k, v in files.items() if str(v).endswith(".geojson")
                 and k not in ("planned", "gapsGeometry")]
    else:
        names = ["network.geojson", "context.geojson", "residential.geojson"]

    result: dict[int, int] = {}
    for name in names:
        path = out_dir / name
        if not path.exists():
            continue
        for feat in json.loads(path.read_text())["features"]:
            result[int(feat["id"])] = int(feat["properties"]["lts"])
    return result


def _sweep_row(name, why, stats, base_lts, variant_lts) -> dict:
    low = stats["low_stress"]
    flipped = {
        fid for fid, lts in variant_lts.items()
        if fid in base_lts and base_lts[fid] != lts
    }
    return {
        "variant": name,
        "why": why,
        "low_stress_miles": low["miles"],
        "islands": low["islands"],
        "largest_island_miles": low["largest_island_miles"],
        "largest_share_pct": low["largest_island_share_pct"],
        "ridable_lts3_miles": stats["ridable_lts3"]["miles"],
        "segments_changed": len(flipped),
        "_flipped_ids": flipped,
    }


def _write_sensitivity_doc(rows, failures, flipped_any, doc_path: Path, params: Params) -> None:
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Sensitivity of the LTS results to parameter choices",
        "",
        f"Ruleset {params['meta.ruleset_version']} (digest `{params.digest}`).",
        "Regenerate with `make sensitivity`.",
        "",
        "Several thresholds in this model are judgement calls, not measurements.",
        "This table shows what each one is worth, so a reader can disagree with a",
        "choice and see immediately how much it would change.",
        "",
        "| variant | low-stress mi | islands | largest island | share | LTS<=3 mi | segments changed |",
        "|---|--:|--:|--:|--:|--:|--:|",
    ]
    for r in rows:
        lines.append(
            f"| `{r['variant']}` | {r['low_stress_miles']:.1f} | {r['islands']} | "
            f"{r['largest_island_miles']:.1f} mi | {r['largest_share_pct']:.1f}% | "
            f"{r['ridable_lts3_miles']:.1f} | {r['segments_changed']} |"
        )
    lines += [
        "",
        "## What each variant tests",
        "",
    ]
    for r in rows[1:]:
        lines.append(f"- **`{r['variant']}`** — {r['why']}")

    if failures:
        lines += [
            "",
            "## Variants that could not be built",
            "",
            "These are results too: the parameter is bounded by a build-time quality",
            "gate, so the sweep could not explore that direction.",
            "",
        ]
        for f in failures:
            lines.append(f"- **`{f['variant']}`** — {f['why']}")
            lines.append(f"  - blocked by: {f['error']}")
    lines += [
        "",
        "## How this feeds the map",
        "",
        f"{len(flipped_any)} segments change LTS under at least one variant. Those are",
        "marked low-confidence in the published data, so the map can distinguish a",
        "rating that is robust from one that rests on a contested threshold.",
        "",
        "## The honest headline",
        "",
        "The island count is a methodology choice, not a measurement — it moves by a",
        "factor of three across defensible variants below. The pairing that survives",
        "the whole sweep is the one worth quoting: the network is close to whole for",
        "confident riders and shattered for everyone else.",
        "",
    ]
    doc_path.write_text("\n".join(lines))

"""Transfer bike facility attributes onto street centrelines.

The centreline layer is the one canonical network: 13,775 features, already
exactly noded (99.7% of nodes in a single connected component). The bike layer
is an independently digitized overlay that shares almost no nodes with it — only
241 of its 1,056 endpoints land within 1 m of a centreline endpoint.

So on-road facilities become centreline *attributes*, and only off-road paths
keep their own geometry, joined to the street network by explicit connector
edges that are drawn on the map so a reader can see and argue with each one.

This replaces the old pipeline's two buffer filters
(``has_parallel_bike_infra`` and the residential de-dup), which existed only to
reconcile the double representation and between them silently deleted 2,417
segments.

Matching strategy. Three options, and why this one:

  per-facility coverage   asking "does this facility cover most of this
                          centreline?" undercounts badly (-32% measured),
                          because one short facility segment legitimately
                          covers only part of a long centreline block
  global buffer union     asking "is this centreline inside the union of all
                          facility buffers?" overcounts (+13%) and cannot
                          check bearing, so it wrongly captured 10 interstate
                          segments that merely run beside a trail
  used here               union only the *nearby, bearing-agreeing* facilities
                          per centreline, then measure that centreline's
                          coverage. Handles several facility segments covering
                          one long block, while keeping the directional check
                          that stops a trail from claiming the arterial it
                          crosses.
"""

from __future__ import annotations

import logging
import math
from typing import Iterable

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely import STRtree
from shapely.geometry import LineString, Point
from shapely.ops import unary_union

from . import io
from .lts import resolve_facility
from .params import Params

log = logging.getLogger(__name__)

#: Id spaces for synthetic features, kept clear of SCLINK so a deep link can
#: never resolve to the wrong feature after a rebuild.
PATH_ID_BASE = 800_000_000
CONNECTOR_ID_BASE = 900_000_000

METRES_PER_MILE = 1609.344


class ConflationError(Exception):
    """Conflation produced a result we are not willing to publish."""


# ---------------------------------------------------------------------------
#  Geometry helpers
# ---------------------------------------------------------------------------

def _segments(geom) -> Iterable[tuple[float, float, float, float]]:
    """Yield consecutive vertex pairs of a (Multi)LineString."""
    parts = getattr(geom, "geoms", [geom])
    for part in parts:
        coords = list(part.coords)
        for (x1, y1), (x2, y2) in zip(coords, coords[1:]):
            yield x1, y1, x2, y2


def line_bearing(geom) -> float | None:
    """Length-weighted mean bearing in [0, 180), or ``None`` if degenerate.

    Two properties the old ``get_line_bearing`` lacked:

    * It averages over every vertex pair rather than taking endpoint-to-endpoint,
      which is meaningless for a curved or C-shaped segment.
    * It averages in doubled-angle space, so bearings of 179 deg and 1 deg — the
      same physical alignment — mean 0 rather than 90. A plain circular mean of
      undirected bearings is wrong at the wrap point.

    The old version also swallowed every exception with a bare ``except:``,
    including KeyboardInterrupt.
    """
    sin_sum = cos_sum = total = 0.0
    for x1, y1, x2, y2 in _segments(geom):
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        if length == 0.0:
            continue
        theta = math.atan2(dx, dy)          # from north, clockwise
        sin_sum += length * math.sin(2 * theta)
        cos_sum += length * math.cos(2 * theta)
        total += length
    if total == 0.0:
        return None
    return math.degrees(math.atan2(sin_sum, cos_sum) / 2.0) % 180.0


def bearing_delta(a: float | None, b: float | None) -> float | None:
    """Smallest angle between two undirected bearings, in [0, 90]."""
    if a is None or b is None:
        return None
    d = abs(a - b) % 180.0
    return min(d, 180.0 - d)


# ---------------------------------------------------------------------------
#  On-road conflation
# ---------------------------------------------------------------------------

def conflate_on_road(
    streets: gpd.GeoDataFrame,
    facilities: gpd.GeoDataFrame,
    params: Params,
) -> gpd.GeoDataFrame:
    """Attach the most protective applicable facility to each centreline.

    Returns ``streets`` with ``fac`` and ``fac_source_ids`` columns added.
    """
    buffer_m = float(params["conflation.buffer_m"])
    min_cov = float(params["conflation.min_coverage"])
    tol_deg = float(params["conflation.bearing_tolerance_deg"])
    excluded = set(int(x) for x in params["conflation.exclude_rdclass"])
    rank = params["facility.rank"]

    min_share = float(params["conflation.min_category_share"])

    st = io.to_working_crs(streets, params).reset_index(drop=True)
    fa = io.to_working_crs(facilities, params).reset_index(drop=True)

    # Only facilities that actually treat the roadway participate. 217 of the 397
    # on-road records are 'Preferred Route', which resolves to fac == "none":
    # signed wayfinding with no physical treatment. Including them would let a
    # no-op category contribute coverage and win the category vote, and would
    # inflate the mileage denominator from 90.3 to 219.7 miles.
    on_road = fa[fa["on_road"] & (fa["fac"] != "none")].reset_index(drop=True)
    untreated = int((fa["on_road"] & (fa["fac"] == "none")).sum())
    log.info(
        "conflating %d treated on-road facilities onto %d centrelines "
        "(%d untreated 'Preferred Route' records excluded) "
        "[buffer %.1f m, coverage %.2f, bearing +/-%.0f deg]",
        len(on_road), len(st), untreated, buffer_m, min_cov, tol_deg,
    )
    if on_road.empty:
        raise ConflationError("no treated on-road facilities to conflate")

    fac_bearing = np.array([line_bearing(g) or np.nan for g in on_road.geometry])
    fac_buffers = on_road.geometry.buffer(buffer_m)
    tree = STRtree(fac_buffers.values)

    st_bearing = np.array([line_bearing(g) or np.nan for g in st.geometry])

    assigned: list[str] = []
    source_ids: list[list[int]] = []
    skipped_bearing = 0
    skipped_coverage = 0

    for i, geom in enumerate(st.geometry):
        if int(st.at[i, "rdclass"]) in excluded:
            # Never credit a facility to an interstate or parkway; a parallel
            # trail must not make a prohibited road look ridable.
            assigned.append("none")
            source_ids.append([])
            continue

        candidates = tree.query(geom)
        if len(candidates) == 0:
            assigned.append("none")
            source_ids.append([])
            continue

        seg_len = geom.length
        if seg_len == 0.0:
            assigned.append("none")
            source_ids.append([])
            continue

        # Keep only candidates running roughly parallel to this centreline.
        keep = []
        for j in candidates:
            delta = bearing_delta(st_bearing[i], fac_bearing[j])
            if delta is None or delta <= tol_deg:
                keep.append(int(j))
        if not keep:
            skipped_bearing += 1
            assigned.append("none")
            source_ids.append([])
            continue

        # Gate on the union of ALL surviving candidates, not per category.
        # Unioning first is what lets two adjacent facility segments — or a bike
        # lane that becomes a buffered lane mid-block — together satisfy one long
        # centreline. Grouping before unioning made each category fail
        # independently and lost 45% of the network.
        corridor = unary_union([fac_buffers.values[j] for j in keep])
        covered = geom.intersection(corridor).length
        if covered < seg_len * min_cov:
            skipped_coverage += 1
            assigned.append("none")
            source_ids.append([])
            continue

        # Now choose the label. A category must contribute a real share of the
        # covered length to be eligible: without this, a facility buffer that
        # merely clips the end of a block (contributing ~0 m) would decide the
        # block's rating.
        contrib: dict[str, float] = {}
        ids_by_cat: dict[str, list[int]] = {}
        for j in keep:
            category = on_road["fac"].values[j]
            contrib[category] = contrib.get(category, 0.0) + geom.intersection(
                fac_buffers.values[j]
            ).length
            ids_by_cat.setdefault(category, []).append(int(on_road.at[j, "id_src"]))

        floor = min_share * covered
        eligible = [c for c, v in contrib.items() if v >= floor]
        if not eligible:
            # Everything was incidental contact; fall back to the single largest
            # contributor rather than dropping a block that did pass the gate.
            eligible = [max(contrib, key=contrib.get)]

        best = resolve_facility(eligible, rank)
        assigned.append(best)
        source_ids.append(sorted(set(ids_by_cat[best])))

    out = streets.copy()
    out["fac"] = assigned
    out["fac_source_ids"] = source_ids

    log.info(
        "conflation: %d centrelines received a facility "
        "(%d candidates rejected on bearing, %d on coverage)",
        int((out["fac"] != "none").sum()), skipped_bearing, skipped_coverage,
    )
    log.info("conflated facility mix: %s", out["fac"].value_counts().to_dict())

    _check_mileage(out, on_road, params)
    return out


def _group_by(values, indices: list[int]):
    """Group candidate indices by their facility category."""
    buckets: dict[str, list[int]] = {}
    for j in indices:
        buckets.setdefault(values[j], []).append(j)
    return buckets.items()


def _check_mileage(
    conflated: gpd.GeoDataFrame, on_road: gpd.GeoDataFrame, params: Params
) -> None:
    """Gate the result on recall and inflation.

    A plain "conflated miles must equal source miles" check would be wrong here.
    Facility segments have a median length of 626 m while centrelines are 118 m,
    so a facility is credited to whole blocks and legitimately yields *more*
    block-mileage than facility-mileage. Comparing the two directly and demanding
    equality would force the thresholds to lose real facilities.

    So two checks that mean something instead:

    recall     fraction of treated facility length that lies within the buffer of
               a centreline we credited. Low recall means we dropped facilities.
    inflation  credited centreline mileage / treated facility mileage. High
               inflation means we credited whole arterials off incidental contact.
    """
    min_recall = float(params["conflation.min_recall"])
    max_inflation = float(params["conflation.max_inflation"])
    buffer_m = float(params["conflation.buffer_m"])

    credited = io.to_working_crs(conflated[conflated["fac"] != "none"], params)
    credited_mi = credited.geometry.length.sum() / METRES_PER_MILE
    source_mi = on_road.geometry.length.sum() / METRES_PER_MILE

    if credited.empty:
        raise ConflationError("no centreline received a facility")

    # Recall: buffer the credited centrelines and measure how much facility
    # length falls inside.
    credited_corridor = unary_union(credited.geometry.buffer(buffer_m).values)
    inside = sum(
        g.intersection(credited_corridor).length for g in on_road.geometry
    )
    total = on_road.geometry.length.sum()
    recall = inside / total if total else 0.0
    inflation = credited_mi / source_mi if source_mi else float("inf")

    log.info(
        "conflation quality: %.1f mi credited from %.1f mi of treated facility "
        "-> recall %.1f%%, inflation %.2fx",
        credited_mi, source_mi, 100 * recall, inflation,
    )

    problems = []
    if recall < min_recall:
        problems.append(
            f"recall {100 * recall:.1f}% is below the {100 * min_recall:.0f}% floor "
            "— real facilities are being dropped; try raising conflation.buffer_m "
            "or lowering conflation.min_coverage"
        )
    if inflation > max_inflation:
        problems.append(
            f"inflation {inflation:.2f}x exceeds {max_inflation:.2f}x "
            "— whole blocks are being credited off incidental contact; try "
            "raising conflation.min_coverage or lowering conflation.buffer_m"
        )
    if problems:
        raise ConflationError("conflation quality gate failed: " + "; ".join(problems))


# ---------------------------------------------------------------------------
#  Off-road paths and their connectors
# ---------------------------------------------------------------------------

def build_off_road(
    streets: gpd.GeoDataFrame,
    facilities: gpd.GeoDataFrame,
    params: Params,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Return (off-road path features, connector features).

    Off-road paths are the only geometry outside the centreline layer, because
    they genuinely are not streets. Each path endpoint within
    ``conflation.connector_max_m`` of a centreline endpoint gets a short
    connector edge, so routing can leave the street network onto a trail without
    the route appearing to teleport.
    """
    max_m = float(params["conflation.connector_max_m"])
    excluded = set(int(x) for x in params["conflation.exclude_rdclass"])

    st = io.to_working_crs(streets, params).reset_index(drop=True)
    fa = io.to_working_crs(facilities, params).reset_index(drop=True)
    off = fa[~fa["on_road"]].reset_index(drop=True)

    # Attach to the nearest point ON a centreline, not to the nearest centreline
    # endpoint. A trail typically meets a street mid-block, so endpoint-only
    # snapping found a neighbour for just 25% of trail endpoints against 53% here.
    # The centreline is split at the projected point in network.py so the
    # connector lands on a real graph node.
    #
    # Interstates and parkways are excluded: a trail running beside I-75 must not
    # be joined to it.
    candidates = st[~st["rdclass"].isin(excluded)].reset_index(drop=True)
    tree = STRtree(candidates.geometry.values)
    log.info(
        "off-road: %d paths, snapping to %d eligible centrelines (max %.0f m)",
        len(off), len(candidates), max_m,
    )

    connectors: list[dict] = []
    attached = 0
    endpoints_total = 0

    for i, geom in enumerate(off.geometry):
        parts = getattr(geom, "geoms", [geom])
        ends: list[tuple[float, float]] = []
        for part in parts:
            coords = list(part.coords)
            if coords:
                ends.append(coords[0])
                ends.append(coords[-1])
        for end in ends:
            endpoints_total += 1
            pt = Point(end)
            nearest = np.atleast_1d(tree.query_nearest(pt))
            if nearest.size == 0:
                continue
            k = int(nearest[0])
            line = candidates.geometry.values[k]
            dist = line.distance(pt)
            if dist > max_m:
                continue
            # Project onto the line so the connector is perpendicular-ish and the
            # split point is well defined.
            snapped = line.interpolate(line.project(pt))
            attached += 1
            if dist < 1e-9:
                continue  # already touching; no connector geometry needed
            connectors.append(
                {
                    "geometry": LineString([end, (snapped.x, snapped.y)]),
                    "fac": "connector",
                    "length_m": float(dist),
                    "path_row": i,
                    "split_street_id": int(candidates.at[k, "id"]),
                    "split_x": float(snapped.x),
                    "split_y": float(snapped.y),
                }
            )

    log.info(
        "off-road: attached %d of %d path endpoints within %.0f m (%d connectors drawn)",
        attached, endpoints_total, max_m, len(connectors),
    )
    if endpoints_total and attached == 0:
        raise ConflationError(
            "no off-road path endpoint attached to the street network; "
            "check conflation.connector_max_m"
        )

    crs = st.crs
    paths_out = off.copy()
    paths_out["id"] = PATH_ID_BASE + np.arange(len(paths_out), dtype="int64")
    paths_out["kind"] = "facility"

    if connectors:
        conn_out = gpd.GeoDataFrame(connectors, geometry="geometry", crs=crs)
        conn_out["id"] = CONNECTOR_ID_BASE + np.arange(len(conn_out), dtype="int64")
        conn_out["kind"] = "connector"
    else:
        conn_out = gpd.GeoDataFrame(
            {
                "geometry": [], "fac": [], "length_m": [], "path_row": [],
                "split_street_id": [], "split_x": [], "split_y": [],
                "id": [], "kind": [],
            },
            geometry="geometry", crs=crs,
        )

    return (
        paths_out.to_crs(streets.crs),
        conn_out.to_crs(streets.crs) if len(conn_out) else conn_out,
    )

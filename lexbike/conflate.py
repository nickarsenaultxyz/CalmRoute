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


def local_bearing_delta(
    street,
    facility,
    street_corridor,
    facility_corridor,
) -> float | None:
    """Alignment where a facility and street are actually near one another.

    Facility records are often much longer than centreline blocks. Comparing a
    short block with the bearing of an entire curved facility is invalid: the
    mean bearing of a loop can be nearly perpendicular to the tangent beside
    that block. Beaumont Centre Circle exposed this as alternating facility/no-
    facility ratings even though one source record continuously covers the
    road.

    Clip both lines to their shared local corridor before comparing them. A
    genuinely perpendicular crossing remains perpendicular, while a long curve
    is judged from the portion beside this specific centreline.
    """
    street_local = street.intersection(facility_corridor)
    facility_local = facility.intersection(street_corridor)
    return bearing_delta(
        line_bearing(street_local),
        line_bearing(facility_local),
    )


def _name_key(value) -> str:
    """Case/whitespace-insensitive key for a scalar source name."""
    if value is None or pd.isna(value):
        return ""
    return " ".join(str(value).casefold().split())


# ---------------------------------------------------------------------------
#  On-road conflation
# ---------------------------------------------------------------------------

def conflate_on_road(
    streets: gpd.GeoDataFrame,
    facilities: gpd.GeoDataFrame,
    params: Params,
    *,
    check_quality: bool = True,
) -> gpd.GeoDataFrame:
    """Attach the most protective applicable facility to each centreline.

    Returns ``streets`` with ``fac`` and ``fac_source_ids`` columns added.

    ``check_quality`` runs the recall/inflation gate. It is calibrated against
    the full existing-facility network and is meaningless on a handful of
    segments, so the scenario pass over funded projects turns it off.
    """
    buffer_m = float(params["conflation.buffer_m"])
    # The named value is an extension of the ordinary buffer, never a cap.
    # Sensitivity runs deliberately raise the ordinary buffer above the
    # production value; in that case the ordinary reach remains authoritative.
    named_buffer_m = max(
        buffer_m,
        float(params.get("conflation.named_corridor_buffer_m", buffer_m)),
    )
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
        "[buffer %.1f m, named-corridor buffer %.1f m, coverage %.2f, "
        "bearing +/-%.0f deg]",
        len(on_road), len(st), untreated, buffer_m, named_buffer_m, min_cov,
        tol_deg,
    )
    if on_road.empty:
        raise ConflationError("no treated on-road facilities to conflate")

    fac_buffers = on_road.geometry.buffer(buffer_m)
    named_fac_buffers = on_road.geometry.buffer(named_buffer_m)
    tree = STRtree(named_fac_buffers.values)
    fac_names = [
        _name_key(value)
        for value in on_road.get(
            "network_name",
            pd.Series("", index=on_road.index, dtype="string"),
        )
    ]

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

        # Keep only candidates running roughly parallel *where the two records
        # overlap*. A facility can be a long curve or loop, so its whole-record
        # mean bearing says nothing about the tangent beside this short block.
        #
        # An exact corridor-name match gets a narrowly larger reach. LFUCG
        # sometimes digitizes one facility line between two one-way carriageways;
        # the ordinary 12 m geometric buffer then credits only the nearer
        # direction. Name agreement scopes the extra reach to the same road.
        street_corridor = geom.buffer(buffer_m)
        named_street_corridor = geom.buffer(named_buffer_m)
        street_name = _name_key(st.at[i, "road_name"]) \
            if "road_name" in st.columns else ""
        standard_corridors = {}
        named_corridors = {}
        had_nearby = False
        for j in candidates:
            j = int(j)
            facility = on_road.geometry.values[j]

            # Preserve the ordinary geometric match exactly when it already
            # covers enough of the block. The named-corridor reach is a fallback,
            # not a competing vote that can replace a more protective facility
            # already credited by the standard pass.
            standard = fac_buffers.values[j]
            if standard.intersects(geom):
                had_nearby = True
                delta = local_bearing_delta(
                    geom,
                    facility,
                    street_corridor,
                    standard,
                )
                if delta is None or delta <= tol_deg:
                    standard_corridors[j] = standard

            same_named_corridor = bool(street_name) and street_name == fac_names[j]
            if not same_named_corridor:
                continue

            named = named_fac_buffers.values[j]
            if not named.intersects(geom):
                continue
            had_nearby = True
            delta = local_bearing_delta(
                geom, facility, named_street_corridor, named
            )
            if delta is None or delta <= tol_deg:
                named_corridors[j] = named

        # First try the normal 12 m match. Only a block it cannot cover may use
        # the exact-name extension, so the extension can fill a missing parallel
        # carriageway but cannot relabel an already matched block.
        standard_covered = 0.0
        if standard_corridors:
            standard_union = unary_union(list(standard_corridors.values()))
            standard_covered = geom.intersection(standard_union).length

        if standard_covered >= seg_len * min_cov:
            candidate_corridors = standard_corridors
            covered = standard_covered
        else:
            candidate_corridors = dict(standard_corridors)
            candidate_corridors.update(named_corridors)
            if candidate_corridors:
                corridor = unary_union(list(candidate_corridors.values()))
                covered = geom.intersection(corridor).length
            else:
                covered = 0.0

        if not candidate_corridors:
            if had_nearby:
                skipped_bearing += 1
            assigned.append("none")
            source_ids.append([])
            continue

        # Gate on the union of ALL surviving candidates, not per category.
        # Unioning first is what lets two adjacent facility segments — or a bike
        # lane that becomes a buffered lane mid-block — together satisfy one long
        # centreline. Grouping before unioning made each category fail
        # independently and lost 45% of the network.
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
        for j in candidate_corridors:
            category = on_road["fac"].values[j]
            contrib[category] = contrib.get(category, 0.0) + geom.intersection(
                candidate_corridors[j]
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

    if check_quality:
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

def _find_attachments(part, candidates, tree, max_m, spacing_m, merge_m):
    """Points along a path where it can join another line.

    Returns ``[(distance_along_path, snapped_point_on_target, target_index,
    source_vertex)]``.

    Three properties this needs, each learned by getting it wrong:

    *Every* nearby target, not just the closest. A trail running between two
    streets must join both; querying only the nearest silently picks one and
    leaves the other unreachable.

    One best candidate per target, with light thinning between interior
    candidates. A path beside one long road connects once rather than at every
    vertex. Endpoints are exempt from the thinning window: applying it there
    drops obvious entrances and exits, including the Hiltonia Park connection
    at Baptist Health.

    Endpoints always attach if anything is in range. A trail's ends are where it
    most obviously meets the network, and they must never be thinned away.
    """
    coords = list(part.coords)
    if len(coords) < 2:
        return []

    # target index -> (gap, distance_along, snapped point)
    best: dict[int, tuple[float, float, Point]] = {}

    for idx, (x, y) in enumerate(coords):
        pt = Point(x, y)
        # Every candidate within reach, not merely the nearest one.
        for k in tree.query(pt.buffer(max_m)):
            k = int(k)
            line = candidates.geometry.values[k]
            gap = line.distance(pt)
            if gap > max_m:
                continue
            is_end = idx == 0 or idx == len(coords) - 1
            # An endpoint outranks a mid-path vertex for the same target.
            score = gap - (max_m if is_end else 0.0)
            prior = best.get(k)
            if prior is None or score < prior[0]:
                best[k] = (score, part.project(pt),
                           line.interpolate(line.project(pt)), pt)

    found = [(along, snapped, k, src) for k, (_, along, snapped, src) in best.items()]
    found.sort(key=lambda t: t[0])

    # First remove true duplicate geometry: different target records sometimes
    # describe the same physical junction.
    merged = []
    for item in found:
        if (
            merged
            and item[3].distance(merged[-1][3]) <= merge_m
            and item[1].distance(merged[-1][1]) <= merge_m
        ):
            continue
        merged.append(item)

    # Keep the closest target at each endpoint. One is enough to enter the
    # noded street graph; keeping every nearby centreline would connect a path
    # to several parallel or adjacent roads at once. The old code could keep
    # none because it applied the interior spacing window to endpoints too.
    endpoint_best: dict[int, tuple[float, tuple]] = {}
    for item in merged:
        which = (
            0 if item[0] <= 1e-6
            else 1 if item[0] >= part.length - 1e-6
            else None
        )
        if which is None:
            continue
        gap = item[3].distance(item[1])
        if which not in endpoint_best or gap < endpoint_best[which][0]:
            endpoint_best[which] = (gap, item)
    endpoint_ids = {id(item) for _, item in endpoint_best.values()}

    # Lightly thin interior attachments so a path parallel to a segmented
    # street does not sprout a connector to every block. The closest endpoint
    # attachment is always retained. This repairs the 9 m Baptist
    # Health-to-Hiltonia Park connection without broadly joining every road
    # inside the search radius.
    out = []
    for item in merged:
        is_endpoint = item[0] <= 1e-6 or item[0] >= part.length - 1e-6
        if is_endpoint and id(item) not in endpoint_ids:
            continue
        if out and item[0] - out[-1][0] < spacing_m and not is_endpoint:
            continue
        out.append(item)
    return out


def _with_max_spacing(cuts: list[float], total: float, max_edge_m: float) -> list[float]:
    """Add cuts so no resulting piece is longer than ``max_edge_m``."""
    out = list(cuts)
    edges = [0.0, *cuts, total]
    for a, b in zip(edges, edges[1:]):
        gap = b - a
        if gap <= max_edge_m:
            continue
        n = int(gap // max_edge_m)
        step = gap / (n + 1)
        out.extend(a + step * (i + 1) for i in range(n))
    return sorted(out)


def _find_path_junctions(part, self_index, off, path_tree, max_m):
    """Points where this path comes close to a *different* path.

    Returns the same shape as :func:`_find_attachments`, with the target index
    negated so the caller can tell a path target from a street target. Cutting
    both paths at the meeting point is what lets a route turn from one trail
    onto another.
    """
    if path_tree is None:
        return []
    out = []
    seen: set[int] = set()
    coords = list(part.coords)
    for x, y in coords:
        pt = Point(x, y)
        for k in path_tree.query(pt.buffer(max_m)):
            k = int(k)
            if k == self_index or k in seen:
                continue
            other = off.geometry.values[k]
            if other.distance(pt) > max_m:
                continue
            seen.add(k)
            out.append((part.project(pt),
                        other.interpolate(other.project(pt)),
                        -(k + 1),
                        pt))
    return out


def build_off_road(
    streets: gpd.GeoDataFrame,
    facilities: gpd.GeoDataFrame,
    params: Params,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Return (off-road path features, connector features).

    Off-road paths are the only geometry outside the centreline layer, because
    they genuinely are not streets.

    Attachment happens ALONG a path, not only at its two ends. Attaching only at
    the ends leaves a trail as a single graph edge: a router can enter it at one
    extremity and leave at the other, and nowhere else. Measured on this data
    that made 34.3 of 38.8 path miles unusable for anything but end-to-end
    travel -- the longest trail was a single 4.96-mile edge -- while 188 points
    along those trails sat within 25 m of a street a rider could obviously join.
    Routes went the long way round rather than use a trail passing metres from
    the destination.

    So every vertex within ``conflation.connector_max_m`` of an eligible
    centreline is a candidate junction. Interior candidates are thinned by
    ``conflation.attach_spacing_m`` so a trail running parallel to a street does
    not sprout a connector at every block; the closest eligible target at each
    endpoint is always kept. The path is cut at each surviving point, which is
    what turns it into something a route can join partway along.
    """
    from .network import cut_line

    max_m = float(params["conflation.connector_max_m"])
    spacing_m = float(params["conflation.attach_spacing_m"])
    merge_m = float(params["conflation.attach_merge_m"])
    max_edge_m = float(params["conflation.max_path_edge_m"])
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

    # Paths are targets too. Two trails meeting is a junction a rider uses, and
    # attaching only to streets left 33 pairs of trails touching within 8 m
    # while sharing no node -- so a route could not turn from one onto the other.
    path_geoms = list(off.geometry.values)
    path_tree = STRtree(path_geoms) if path_geoms else None
    log.info(
        "off-road: %d paths, snapping to %d eligible centrelines (max %.0f m)",
        len(off), len(candidates), max_m,
    )

    connectors: list[dict] = []
    path_rows: list[dict] = []
    attach_count = 0

    for i, geom in enumerate(off.geometry):
        template = off.iloc[i].to_dict()
        for part in getattr(geom, "geoms", [geom]):
            if part.length <= 0:
                continue
            attachments = _find_attachments(
                part, candidates, tree, max_m, spacing_m, merge_m)
            attachments += _find_path_junctions(
                part, i, off, path_tree, max_m)
            # One list, sorted, so the cut points below stay in order.
            attachments.sort(key=lambda t: t[0])
            attach_count += len(attachments)

            # Cut the path at every interior attachment so those points become
            # real nodes. Endpoints need no cut; they already are nodes.
            interior = sorted(
                d for d, _, _, _ in attachments if 1e-6 < d < part.length - 1e-6)
            # Guarantee a node at least every max_path_edge_m, so a long rural
            # trail is not one edge a route can only leave at its ends.
            interior = _with_max_spacing(interior, part.length, max_edge_m)
            pieces = cut_line(part, interior) if interior else [part]
            for piece in pieces:
                row = dict(template)
                row["geometry"] = piece
                path_rows.append(row)

            for dist_along, snapped, street_idx, source_pt in attachments:
                # Use the vertex the match was found at, NOT
                # part.interpolate(dist_along). `project` is ambiguous where a
                # path doubles back on itself, so round-tripping through the
                # along-distance can land on a completely different part of the
                # trail -- which produced a 1,476 m "connector", a phantom
                # low-stress shortcut across the city.
                on_path = source_pt
                gap = on_path.distance(snapped)
                if gap < 1e-9:
                    continue     # already coincident; no connector geometry needed
                if gap > max_m:
                    # Belt and braces: nothing beyond the search radius can be a
                    # short link, whatever the geometry did.
                    continue
                to_path = street_idx < 0
                connectors.append({
                    "geometry": LineString(
                        [(on_path.x, on_path.y), (snapped.x, snapped.y)]),
                    "fac": "connector",
                    "length_m": float(gap),
                    "path_row": i,
                    # A path-to-path connector has no centreline to split; the
                    # -1 sentinel tells network.split_for_connectors to skip it.
                    "split_street_id": -1 if to_path
                                       else int(candidates.at[street_idx, "id"]),
                    "split_x": float(snapped.x),
                    "split_y": float(snapped.y),
                })

    # A very small number of longer campus cut-throughs have been reviewed from
    # the map and configured explicitly. Keep them out of `connector_max_m`:
    # raising the citywide search radius to reach an 87 m campus connection
    # would create hundreds of unreviewed shortcuts across blocks and parcels.
    reviewed = _reviewed_connectors(off, candidates, params)
    connectors.extend(reviewed)

    log.info(
        "off-road: %d paths -> %d pieces, %d attachment points, %d connectors "
        "(%d reviewed; automatic max %.0f m, interior spacing %.0f m)",
        len(off), len(path_rows), attach_count, len(connectors), len(reviewed),
        max_m, spacing_m,
    )
    if len(off) and attach_count == 0:
        raise ConflationError(
            "no off-road path attached to the street network; "
            "check conflation.connector_max_m"
        )

    crs = st.crs
    paths_out = gpd.GeoDataFrame(path_rows, geometry="geometry", crs=crs) \
        if path_rows else off.copy()
    paths_out = paths_out.reset_index(drop=True)
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


def _reviewed_connectors(
    paths: gpd.GeoDataFrame,
    streets: gpd.GeoDataFrame,
    params: Params,
) -> list[dict]:
    """Build only the path-to-street links explicitly listed in params.

    A configured source coordinate identifies an existing path endpoint within
    three metres; it never becomes geometry itself. Using the real endpoint
    preserves exact graph topology when upstream OSM coordinates carry more
    precision than the human-reviewed configuration. The target is the nearest
    segment of one named LFUCG street and must remain within the per-link cap.
    """
    specs = params.get("conflation.reviewed_connectors", [])
    if not specs:
        return []

    source_points = gpd.GeoSeries(
        [Point(spec["source"]) for spec in specs],
        crs=4326,
    ).to_crs(paths.crs)

    endpoint_rows: list[tuple[Point, int]] = []
    for path_row, geom in enumerate(paths.geometry):
        for part in getattr(geom, "geoms", [geom]):
            coords = list(part.coords)
            if len(coords) >= 2:
                endpoint_rows.extend([
                    (Point(coords[0]), path_row),
                    (Point(coords[-1]), path_row),
                ])

    out = []
    for spec, requested in zip(specs, source_points):
        name = str(spec["name"])
        target_name = str(spec["target_street"])
        max_m = float(spec["max_m"])

        if not endpoint_rows:
            raise ConflationError(
                f"reviewed connector {name!r}: no path endpoints are available"
            )
        source, path_row = min(
            endpoint_rows, key=lambda item: item[0].distance(requested))
        source_gap = source.distance(requested)
        if source_gap > 3.0:
            raise ConflationError(
                f"reviewed connector {name!r}: configured source is "
                f"{source_gap:.1f} m from the nearest path endpoint"
            )

        matches = streets[streets["road_name"] == target_name]
        if matches.empty:
            raise ConflationError(
                f"reviewed connector {name!r}: target street "
                f"{target_name!r} is unavailable"
            )
        target_pos = min(
            matches.index, key=lambda i: matches.at[i, "geometry"].distance(source))
        target_line = matches.at[target_pos, "geometry"]
        snapped = target_line.interpolate(target_line.project(source))
        gap = source.distance(snapped)
        if gap > max_m:
            raise ConflationError(
                f"reviewed connector {name!r}: target moved to {gap:.1f} m "
                f"(reviewed maximum {max_m:.1f} m)"
            )

        out.append({
            "geometry": LineString([(source.x, source.y), (snapped.x, snapped.y)]),
            "fac": "connector",
            "length_m": float(gap),
            "path_row": int(path_row),
            "split_street_id": int(matches.at[target_pos, "id"]),
            "split_x": float(snapped.x),
            "split_y": float(snapped.y),
            "connector_reviewed": True,
            "connector_name": name,
        })
        log.info(
            "reviewed connector: %s (%.1f m to %s)",
            name, gap, target_name,
        )

    return out

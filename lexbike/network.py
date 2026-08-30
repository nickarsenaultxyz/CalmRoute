"""Graph construction, low-stress island labelling, and barrier ranking.

Three things this module deliberately does differently from the old
``lts_connectivity_analysis.py``:

**No grid snapping.** ``snap_to_grid`` rounded each coordinate to a multiple of
the tolerance, so endpoints 0.1 m apart landed in different cells if they
straddled a boundary, while points 11 m apart on a diagonal shared one. It
manufactured islands in both directions. The LFUCG centrelines are already
exactly noded — 99.7% of nodes fall in a single component — so node identity is
just equality of rounded coordinates, at the same precision the geometry is
exported with.

**Nodes are not endpoint-only.** A trail or reviewed street endpoint meeting a
street mid-block used to create no node at all, severing the network invisibly.
Centrelines needing that mid-block node are split there.

**Barriers are ranked and written.** ``identify_barriers`` was the most
expensive computation in the old script and its result was discarded — printed
once, never saved or mapped — while "identify strategic barrier crossings" was
stated as improvement priority #1.
"""

from __future__ import annotations

import logging
from collections import defaultdict

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd
from pyproj import Transformer
from shapely.geometry import LineString, Point

from . import io
from . import lts as lts_mod
from .params import Params

log = logging.getLogger(__name__)

METRES_PER_MILE = 1609.344

#: Id space for the second and later pieces of a centreline split to host a
#: trail connector. The first piece keeps its parent SCLINK, so an existing deep
#: link still resolves to a real piece of the same street.
SPLIT_ID_BASE = 700_000_000

#: Separate id ranges keep topology-only pieces stable when ordinary trail
#: connector counts change. All remain below the 800M/900M ranges reserved for
#: supplementary paths and their connector edges in ``conflate.py``.
RING_SPLIT_ID_BASE = 710_000_000
JUNCTION_SPLIT_ID_BASE = 720_000_000
CLOSURE_SPLIT_ID_BASE = 725_000_000

NO_ISLAND = -1


class NetworkError(Exception):
    """The graph could not be built in a form we are willing to publish."""


# ---------------------------------------------------------------------------
#  Splitting
# ---------------------------------------------------------------------------

def split_for_connectors(
    streets: gpd.GeoDataFrame,
    connectors: gpd.GeoDataFrame,
    params: Params,
) -> gpd.GeoDataFrame:
    """Split centrelines wherever the graph needs a real junction node.

    Three lossless normalizations happen together so a street is rebuilt only
    once:

    * trail connectors landing mid-block;
    * an individually reviewed centreline endpoint that exactly matches another
      centreline's interior vertex at published coordinate precision; and
    * true closed street rings, cut into thirds so an undirected ``Graph`` does
      not reduce the whole street to an unusable self-edge.

    This deliberately does *not* snap merely nearby streets. Close cul-de-sacs,
    divided roads and grade-separated crossings are common enough that those
    links require individual review.
    """
    wanted: dict[int, list[tuple[float, float]]] = defaultdict(list)
    if connectors is not None and not connectors.empty \
            and "split_street_id" in connectors.columns:
        for _, row in connectors.iterrows():
            street_id = int(row["split_street_id"])
            if street_id < 0:
                continue      # path-to-path connector; no centreline to split
            wanted[street_id].append((row["split_x"], row["split_y"]))

    st_m = io.to_working_crs(streets, params)
    to_source_crs = Transformer.from_crs(
        st_m.crs, streets.crs, always_xy=True
    )
    by_id = {int(v): i for i, v in enumerate(streets["id"].values)}

    cuts_by_pos: dict[int, set[float]] = defaultdict(set)
    reasons_by_pos: dict[int, set[str]] = defaultdict(set)

    # Connector coordinates are already stored in the working CRS.
    for street_id, points in wanted.items():
        pos = by_id.get(street_id)
        if pos is None:
            continue
        line = st_m.geometry.values[pos]
        for point in points:
            cuts_by_pos[pos].add(float(line.project(Point(point))))
        reasons_by_pos[pos].add("connector")

    decimals = int(params["meta.coord_decimals"])

    # A closed LineString has one endpoint and would otherwise be discarded as
    # a self-edge. Three pieces form a real cycle. Two are insufficient because
    # they have the same u/v pair and ``nx.Graph`` keeps only one parallel edge.
    for pos, (source_line, line_m) in enumerate(
        zip(streets.geometry.values, st_m.geometry.values)
    ):
        if source_line.geom_type != "LineString" or not source_line.is_ring:
            continue
        if line_m.length <= 1e-6:
            continue
        cuts_by_pos[pos].update((line_m.length / 3, 2 * line_m.length / 3))
        reasons_by_pos[pos].add("ring")

    # Node identity is defined at export precision. A street endpoint matching
    # another street's *true interior* vertex is a candidate T-junction. LFUCG
    # has no bridge/layer field, so even an exact match is admitted only when
    # the two source records are pinned in reviewed_exact_junctions.
    endpoint_rows: dict[tuple[float, float], set[int]] = defaultdict(set)
    for pos, geom in enumerate(streets.geometry.values):
        ends = _endpoints(geom, decimals)
        if ends is None:
            continue
        endpoint_rows[ends[0]].add(pos)
        endpoint_rows[ends[1]].add(pos)

    reviewed_junctions = {}
    for spec in params.get("network.reviewed_exact_junctions", []):
        pair = (
            int(spec["target_street_id"]),
            int(spec["endpoint_street_id"]),
        )
        if pair in reviewed_junctions:
            raise NetworkError(
                f"duplicate reviewed exact junction for street ids {pair}"
            )
        reviewed_junctions[pair] = spec

    found_junctions: set[tuple[int, int]] = set()
    unreviewed_junctions: set[tuple[int, int, tuple[float, float]]] = set()
    exact_junctions = 0
    for pos, (source_line, line_m) in enumerate(
        zip(streets.geometry.values, st_m.geometry.values)
    ):
        if source_line.geom_type != "LineString":
            continue
        target_ends = _endpoints(source_line, decimals)
        if target_ends is None:
            continue
        target_id = int(streets["id"].values[pos])
        source_coords = list(source_line.coords)
        metric_coords = list(line_m.coords)
        along = [0.0]
        for first, second in zip(metric_coords, metric_coords[1:]):
            along.append(
                along[-1]
                + float(np.hypot(second[0] - first[0], second[1] - first[1]))
            )
        for vertex_index, coord in enumerate(source_coords[1:-1], start=1):
            key = (round(coord[0], decimals), round(coord[1], decimals))
            # LFUCG often repeats an endpoint as an early/late vertex with
            # slightly different raw precision. It is already the same graph
            # node and must not create a sub-precision split piece.
            if key in target_ends:
                continue
            for other in endpoint_rows.get(key, ()):
                if other == pos:
                    continue
                endpoint_id = int(streets["id"].values[other])
                pair = (target_id, endpoint_id)
                spec = reviewed_junctions.get(pair)
                if spec is None:
                    unreviewed_junctions.add((target_id, endpoint_id, key))
                    continue

                target_name = str(streets.iloc[pos].get("road_name") or "")
                endpoint_name = str(streets.iloc[other].get("road_name") or "")
                if target_name != str(spec["target_street_name"]):
                    raise NetworkError(
                        f"reviewed exact junction {spec['name']!r}: target "
                        f"street {target_id} is {target_name!r}"
                    )
                if endpoint_name != str(spec["endpoint_street_name"]):
                    raise NetworkError(
                        f"reviewed exact junction {spec['name']!r}: endpoint "
                        f"street {endpoint_id} is {endpoint_name!r}"
                    )
                requested_key = tuple(
                    round(float(value), decimals) for value in spec["point"]
                )
                if key != requested_key:
                    raise NetworkError(
                        f"reviewed exact junction {spec['name']!r}: junction "
                        f"moved from {requested_key} to {key}"
                    )

                # Use the vertex's cumulative position, not ``project``.
                # Projection is ambiguous when a line doubles back over itself
                # and can return the same coordinate's earlier occurrence.
                cuts_by_pos[pos].add(along[vertex_index])
                reasons_by_pos[pos].add("junction")
                if pair not in found_junctions:
                    exact_junctions += 1
                    found_junctions.add(pair)

    if unreviewed_junctions:
        examples = sorted(unreviewed_junctions)[:5]
        raise NetworkError(
            f"{len(unreviewed_junctions)} new exact endpoint-to-interior "
            f"junction(s) need bridge/layer review (e.g. {examples})"
        )
    applicable_junctions = {
        pair for pair in reviewed_junctions
        if pair[0] in by_id and pair[1] in by_id
    }
    missing_junctions = applicable_junctions - found_junctions
    if missing_junctions:
        raise NetworkError(
            "reviewed exact junction(s) are no longer present in the source: "
            + ", ".join(str(pair) for pair in sorted(missing_junctions))
        )

    keep_rows: list[int] = []
    new_geoms: list[LineString] = []
    new_parent: list[int] = []
    new_reason: list[str] = []
    split_count = 0
    merged_slivers = 0

    for pos in sorted(cuts_by_pos):
        street_id = int(streets["id"].values[pos])
        line = st_m.geometry.values[pos]
        dists = sorted(cuts_by_pos[pos])
        dists = [d for d in dists if 1e-6 < d < line.length - 1e-6]
        if not dists:
            continue
        pieces = cut_line(line, dists)
        pieces, merged = _merge_collapsed_pieces(
            pieces, to_source_crs, decimals
        )
        merged_slivers += merged
        if (
            len(pieces) == 1
            and _metric_piece_collapses(pieces[0], to_source_crs, decimals)
        ):
            raise NetworkError(
                f"street id {street_id} collapses entirely to one published node"
            )
        if len(pieces) < 2:
            continue
        keep_rows.append(pos)
        new_geoms.extend(pieces)
        new_parent.extend([street_id] * len(pieces))
        reason = (
            "connector" if "connector" in reasons_by_pos[pos]
            else "ring" if "ring" in reasons_by_pos[pos]
            else "junction"
        )
        new_reason.extend([reason] * len(pieces))
        split_count += 1

    if not keep_rows:
        _assert_no_collapsed_features(streets, decimals)
        log.info("no centreline topology split needed")
        return streets

    # Rebuild: replace each split parent with its pieces, inheriting attributes.
    frames = [streets.drop(streets.index[keep_rows])]
    rebuilt = []
    next_id = {
        "connector": SPLIT_ID_BASE,
        "ring": RING_SPLIT_ID_BASE,
        "junction": JUNCTION_SPLIT_ID_BASE,
    }
    for parent_id, group, reason in _group_pieces(new_parent, new_geoms, new_reason):
        template = streets.iloc[by_id[parent_id]]
        for k, geom in enumerate(group):
            row = template.copy()
            row["geometry"] = geom
            if k > 0:
                row["id"] = next_id[reason]
                next_id[reason] += 1
            rebuilt.append(row)

    pieces_gdf = gpd.GeoDataFrame(rebuilt, crs=st_m.crs).to_crs(streets.crs)
    out = pd.concat([frames[0], pieces_gdf], ignore_index=True)
    out = gpd.GeoDataFrame(out, geometry="geometry", crs=streets.crs)

    _assert_no_collapsed_features(out, decimals)

    log.info(
        "topology normalization: split %d centrelines into %d pieces "
        "(%d connector targets, %d rings, %d reviewed exact interior "
        "junction(s); "
        "merged %d sub-precision cuts)",
        split_count,
        len(new_geoms),
        sum("connector" in reasons for reasons in reasons_by_pos.values()),
        sum("ring" in reasons for reasons in reasons_by_pos.values()),
        exact_junctions,
        merged_slivers,
    )
    return out


def cut_line(line: LineString, dists: list[float]) -> list[LineString]:
    """Split ``line`` at the given along-line distances."""
    coords = list(line.coords)
    pieces: list[LineString] = []
    current = [coords[0]]
    remaining = list(dists)
    travelled = 0.0

    for a, b in zip(coords, coords[1:]):
        seg = LineString([a, b])
        seg_len = seg.length
        while remaining and travelled < remaining[0] <= travelled + seg_len:
            d = remaining.pop(0)
            pt = line.interpolate(d)
            current.append((pt.x, pt.y))
            if len(current) >= 2:
                pieces.append(LineString(current))
            current = [(pt.x, pt.y)]
        current.append(b)
        travelled += seg_len

    if len(current) >= 2:
        pieces.append(LineString(current))
    return [p for p in pieces if p.length > 1e-9]


def _merge_collapsed_pieces(
    pieces: list[LineString],
    to_source_crs: Transformer,
    decimals: int,
) -> tuple[list[LineString], int]:
    """Absorb cuts that do not create a distinct published graph node.

    Two connector or junction cuts can be centimetres apart yet round to the
    same exported coordinate. Keeping the tiny arc between them creates a
    self-edge; dropping it loses source geometry. Merge it into an adjacent arc
    instead, preserving every vertex and the parent street's complete length.
    """

    out: list[LineString] = []
    leading: LineString | None = None
    merged = 0
    for piece in pieces:
        if _metric_piece_collapses(piece, to_source_crs, decimals):
            merged += 1
            if out:
                out[-1] = LineString([
                    *out[-1].coords,
                    *list(piece.coords)[1:],
                ])
            elif leading is None:
                leading = piece
            else:
                leading = LineString([
                    *leading.coords,
                    *list(piece.coords)[1:],
                ])
            continue

        if leading is not None:
            piece = LineString([
                *list(leading.coords)[:-1],
                *piece.coords,
            ])
            leading = None
        out.append(piece)

    if leading is not None:
        # Every piece collapsed into one published node. Preserve it so the
        # caller's invariant raises instead of silently deleting the street.
        if out:
            out[-1] = LineString([
                *out[-1].coords,
                *list(leading.coords)[1:],
            ])
        else:
            out.append(leading)
    return out, merged


def _metric_piece_collapses(
    piece: LineString,
    to_source_crs: Transformer,
    decimals: int,
) -> bool:
    def published_key(coord) -> tuple[float, float]:
        x, y = to_source_crs.transform(coord[0], coord[1])
        return round(x, decimals), round(y, decimals)

    return published_key(piece.coords[0]) == published_key(piece.coords[-1])


def _group_pieces(
    parents: list[int], geoms: list[LineString], reasons: list[str]
):
    buckets: dict[int, tuple[list[LineString], str]] = {}
    for parent, geom, reason in zip(parents, geoms, reasons):
        if parent not in buckets:
            buckets[parent] = ([], reason)
        buckets[parent][0].append(geom)
    return (
        (parent, group, reason)
        for parent, (group, reason) in buckets.items()
    )


def apply_reviewed_street_closures(
    streets: gpd.GeoDataFrame,
    params: Params,
) -> gpd.GeoDataFrame:
    """Mark municipal geometry covered by individually reviewed closures.

    A visible paved line is not necessarily public bicycle access. Each closure
    pins the source id/name and a narrow expected-length range. A closure may
    cover a complete source record or a reviewed portion ending at a configured
    boundary. Partial closures retain the source id on the closed piece and use
    an explicit stable id for the public remainder.

    This runs before ordinary topology splitting so every future child piece
    inherits the access marker; :func:`pipeline.classify` forces only marked
    pieces to LTS 0.
    """
    specs = params.get("network.reviewed_street_closures", [])
    if not specs:
        return streets
    if streets.empty or not streets["id"].is_unique:
        raise NetworkError(
            "reviewed street closures require non-empty, unique source ids"
        )

    out = streets.copy()
    if "road_bike_ok" not in out.columns:
        out["road_bike_ok"] = True
    else:
        out["road_bike_ok"] = out["road_bike_ok"].fillna(True).astype(bool)
    out["access_reviewed"] = out.get(
        "access_reviewed", pd.Series(False, index=out.index)
    ).fillna(False).astype(bool)

    seen: set[int] = set()
    for spec in specs:
        street_id = int(spec["id"])
        name = str(spec["name"])
        if street_id in seen:
            raise NetworkError(
                f"duplicate reviewed street closure for id {street_id}"
            )
        seen.add(street_id)
        by_id = {int(value): pos for pos, value in enumerate(out["id"].values)}
        pos = by_id.get(street_id)
        if pos is None:
            raise NetworkError(
                f"reviewed street closure {name!r}: street id {street_id} "
                "is unavailable"
            )
        actual_name = str(out.iloc[pos].get("road_name") or "")
        expected_name = str(spec["street_name"])
        if actual_name != expected_name:
            raise NetworkError(
                f"reviewed street closure {name!r}: street id {street_id} "
                f"is {actual_name!r}, expected {expected_name!r}"
            )
        source_geom = out.geometry.values[pos]
        if source_geom.geom_type != "LineString":
            raise NetworkError(
                f"reviewed street closure {name!r}: street id {street_id} "
                "is not a LineString"
            )
        metric_geom = gpd.GeoSeries(
            [source_geom], crs=out.crs
        ).to_crs(params["meta.crs_working"]).iloc[0]
        length_m = float(metric_geom.length)
        min_m = float(spec["min_length_m"])
        max_m = float(spec["max_length_m"])
        if not min_m <= length_m <= max_m:
            raise NetworkError(
                f"reviewed street closure {name!r}: street id {street_id} "
                f"is {length_m:.1f} m, expected {min_m:.1f}-{max_m:.1f} m"
            )

        boundary = spec.get("boundary")
        if boundary is None:
            index = out.index[pos]
            out.at[index, "road_bike_ok"] = False
            out.at[index, "access_reviewed"] = True
            log.info(
                "reviewed street closure: %s marks %d non-routable (%.1f m)",
                name, street_id, length_m,
            )
            continue

        closed_from = str(spec.get("closed_from", "")).lower()
        if closed_from not in {"start", "end"}:
            raise NetworkError(
                f"reviewed street closure {name!r}: closed_from must be "
                "'start' or 'end'"
            )
        public_id = int(spec["public_piece_id"])
        if not CLOSURE_SPLIT_ID_BASE <= public_id < 730_000_000:
            raise NetworkError(
                f"reviewed street closure {name!r}: public piece id "
                f"{public_id} is outside the reserved 725M range"
            )
        if public_id in by_id or public_id in seen:
            raise NetworkError(
                f"reviewed street closure {name!r}: duplicate public piece "
                f"id {public_id}"
            )

        review_points = gpd.GeoSeries(
            [Point(boundary), Point(spec["closed_endpoint"])], crs=out.crs
        ).to_crs(params["meta.crs_working"])
        boundary_m, expected_end_m = review_points.iloc[0], review_points.iloc[1]
        boundary_gap = float(metric_geom.distance(boundary_m))
        max_boundary_m = float(spec["max_boundary_m"])
        if boundary_gap > max_boundary_m:
            raise NetworkError(
                f"reviewed street closure {name!r}: boundary is "
                f"{boundary_gap:.1f} m from street id {street_id} "
                f"(maximum {max_boundary_m:.1f} m)"
            )
        source_end_m = Point(
            metric_geom.coords[0 if closed_from == "start" else -1]
        )
        endpoint_gap = float(source_end_m.distance(expected_end_m))
        max_endpoint_m = float(spec["max_endpoint_m"])
        if endpoint_gap > max_endpoint_m:
            raise NetworkError(
                f"reviewed street closure {name!r}: closed endpoint is "
                f"{endpoint_gap:.1f} m from the pinned source endpoint "
                f"(maximum {max_endpoint_m:.1f} m)"
            )

        cut_at = float(metric_geom.project(boundary_m))
        closed_length_m = (
            cut_at if closed_from == "start" else length_m - cut_at
        )
        min_closed_m = float(spec["min_closed_length_m"])
        max_closed_m = float(spec["max_closed_length_m"])
        if not min_closed_m <= closed_length_m <= max_closed_m:
            raise NetworkError(
                f"reviewed street closure {name!r}: closed piece is "
                f"{closed_length_m:.1f} m, expected "
                f"{min_closed_m:.1f}-{max_closed_m:.1f} m"
            )
        metric_pieces = cut_line(metric_geom, [cut_at])
        if len(metric_pieces) != 2:
            raise NetworkError(
                f"reviewed street closure {name!r}: boundary does not make "
                "two non-empty pieces"
            )
        source_pieces = gpd.GeoSeries(
            metric_pieces, crs=params["meta.crs_working"]
        ).to_crs(out.crs)
        if closed_from == "start":
            closed_geom, public_geom = source_pieces.iloc[0], source_pieces.iloc[1]
        else:
            public_geom, closed_geom = source_pieces.iloc[0], source_pieces.iloc[1]

        closed_row = out.iloc[pos].copy()
        closed_row["geometry"] = closed_geom
        closed_row["road_bike_ok"] = False
        closed_row["access_reviewed"] = True
        public_row = out.iloc[pos].copy()
        public_row["id"] = public_id
        public_row["geometry"] = public_geom
        public_row["road_bike_ok"] = True
        public_row["access_reviewed"] = False
        replacement = gpd.GeoDataFrame(
            [closed_row, public_row], geometry="geometry", crs=out.crs
        )
        out = pd.concat(
            [out.drop(out.index[pos]), replacement], ignore_index=True
        )
        out = gpd.GeoDataFrame(out, geometry="geometry", crs=streets.crs)
        log.info(
            "reviewed street closure: %s splits %d at %.1f m; "
            "public remainder is %d",
            name, street_id, closed_length_m, public_id,
        )
    return out


def add_reviewed_street_links(
    streets: gpd.GeoDataFrame,
    params: Params,
) -> gpd.GeoDataFrame:
    """Add only the short street links explicitly reviewed in ``params.toml``.

    Municipal centrelines sometimes stop on either side of pavement that is
    present in current imagery and in another maintained street source. A
    global endpoint tolerance is unsafe, so each accepted link pins two source
    ids, their expected names, an independently reviewed geometry, and strict
    endpoint/length limits.

    The configured geometry is snapped to the *current* source endpoints after
    passing those gates. That preserves exact graph topology across harmless
    source refreshes instead of leaving two coordinates a fraction apart.
    ``streets`` is expected to be classified already; ordinary display fields
    are inherited conservatively from the more stressful incident arm while
    the reviewed LTS is explicit.
    """
    specs = params.get("network.reviewed_street_links", [])
    if not specs:
        return streets
    if streets.empty:
        raise NetworkError(
            "reviewed street links configured but no streets are available"
        )
    if streets.crs is None or streets.crs.to_epsg() != 4326:
        raise NetworkError("reviewed street links require streets in EPSG:4326")
    if not streets["id"].is_unique:
        raise NetworkError("reviewed street links require unique source street ids")

    decimals = int(params["meta.coord_decimals"])
    by_id = {int(value): pos for pos, value in enumerate(streets["id"].values)}
    existing_ids = set(by_id)

    endpoint_degree: dict[tuple[float, float], int] = defaultdict(int)
    for geom in streets.geometry.values:
        ends = _endpoints(geom, decimals)
        if ends is None:
            continue
        endpoint_degree[ends[0]] += 1
        endpoint_degree[ends[1]] += 1

    work = io.to_working_crs(streets, params).reset_index(drop=True)
    configured = gpd.GeoSeries(
        [LineString(spec["geometry"]) for spec in specs],
        crs=4326,
    ).to_crs(work.crs)

    additions = []
    configured_ids: set[int] = set()
    for spec, requested_m in zip(specs, configured):
        name = str(spec["name"])
        link_id = int(spec["id"])
        street_ids = [int(value) for value in spec["street_ids"]]
        street_names = [str(value) for value in spec["street_names"]]
        max_endpoint_m = float(spec["max_endpoint_m"])
        max_length_m = float(spec["max_length_m"])

        if len(street_ids) != 2 or len(street_names) != 2:
            raise NetworkError(
                f"reviewed street link {name!r}: street_ids and street_names "
                "must each contain exactly two values"
            )
        if link_id in existing_ids or link_id in configured_ids:
            raise NetworkError(
                f"reviewed street link {name!r}: duplicate id {link_id}"
            )
        configured_ids.add(link_id)

        positions = []
        actual_coords = []
        rounded_ends = []
        for side, (street_id, expected_name) in enumerate(
            zip(street_ids, street_names)
        ):
            pos = by_id.get(street_id)
            if pos is None:
                raise NetworkError(
                    f"reviewed street link {name!r}: street id {street_id} "
                    "is unavailable"
                )
            name_value = streets.iloc[pos].get("road_name")
            actual_name = (
                "" if name_value is None or pd.isna(name_value)
                else str(name_value)
            )
            if actual_name != expected_name:
                raise NetworkError(
                    f"reviewed street link {name!r}: street id {street_id} "
                    f"is {actual_name!r}, expected {expected_name!r}"
                )

            source_geom = streets.geometry.values[pos]
            metric_geom = work.geometry.values[pos]
            if (
                source_geom.geom_type != "LineString"
                or metric_geom.geom_type != "LineString"
            ):
                raise NetworkError(
                    f"reviewed street link {name!r}: street id {street_id} "
                    "is not a LineString"
                )
            source_candidates = [source_geom.coords[0], source_geom.coords[-1]]
            metric_candidates = [
                Point(metric_geom.coords[0]),
                Point(metric_geom.coords[-1]),
            ]
            requested_end = Point(
                requested_m.coords[0] if side == 0 else requested_m.coords[-1]
            )
            endpoint_index = min(
                range(2), key=lambda i: metric_candidates[i].distance(requested_end)
            )
            gap = float(metric_candidates[endpoint_index].distance(requested_end))
            if gap > max_endpoint_m:
                raise NetworkError(
                    f"reviewed street link {name!r}: configured end is {gap:.1f} m "
                    f"from street id {street_id} (maximum {max_endpoint_m:.1f} m)"
                )

            coord = tuple(source_candidates[endpoint_index])
            rounded = (round(coord[0], decimals), round(coord[1], decimals))
            positions.append(pos)
            actual_coords.append(coord)
            rounded_ends.append(rounded)

        # If the municipal source has acquired the missing connection, do not
        # publish a duplicate edge. This is the one safe stale-config no-op.
        if rounded_ends[0] == rounded_ends[1]:
            log.info("reviewed street link already present upstream: %s", name)
            continue
        for street_id, rounded in zip(street_ids, rounded_ends):
            if endpoint_degree.get(rounded, 0) != 1:
                raise NetworkError(
                    f"reviewed street link {name!r}: selected endpoint of street "
                    f"id {street_id} is no longer dangling; review the source change"
                )

        configured_coords = list(LineString(spec["geometry"]).coords)
        geometry = LineString([
            actual_coords[0],
            *configured_coords[1:-1],
            actual_coords[1],
        ])
        length_m = float(
            gpd.GeoSeries([geometry], crs=4326).to_crs(work.crs).iloc[0].length
        )
        if length_m > max_length_m:
            raise NetworkError(
                f"reviewed street link {name!r}: geometry is {length_m:.1f} m "
                f"(maximum {max_length_m:.1f} m)"
            )

        # Copy the incident arm with the higher LTS so ancillary fields remain
        # conservative; then override every field whose meaning is specific to
        # the reviewed link.
        template_pos = max(
            positions,
            key=lambda pos: int(streets.iloc[pos].get("lts", spec["lts"])),
        )
        row = streets.iloc[template_pos].copy()
        row["id"] = link_id
        row["geometry"] = geometry
        row["road_name"] = str(spec["road_name"])
        row["lts"] = int(spec["lts"])
        row["road_bike_ok"] = bool(spec.get("road_bike_ok", True))
        row["access_reviewed"] = not row["road_bike_ok"]
        row["fac"] = "none"
        # Geometry was clipped from the identified OSM street ways, so retain
        # ODbL provenance in the downloadable feature and public attribution.
        row["source"] = "osm"
        row["osm_role"] = "reviewed_street_link"
        row["connector_reviewed"] = True
        row["connector_name"] = name
        additions.append(row)
        log.info(
            "reviewed street link: %s joins %d to %d (%.1f m, LTS %d)",
            name, street_ids[0], street_ids[1], length_m, int(spec["lts"]),
        )

    if not additions:
        return streets
    extra = gpd.GeoDataFrame(additions, geometry="geometry", crs=streets.crs)
    out = pd.concat([streets, extra], ignore_index=True)
    return gpd.GeoDataFrame(out, geometry="geometry", crs=streets.crs)


# ---------------------------------------------------------------------------
#  Graph
# ---------------------------------------------------------------------------

def _endpoints(geom, decimals: int) -> tuple[tuple, tuple] | None:
    """First and last coordinate, rounded to the export precision.

    A MultiLineString with disjoint parts has no meaningful single endpoint
    pair; the old ``get_endpoints`` concatenated all parts' coordinates and took
    first/last, inventing a pair that corresponded to no actual line. Such
    geometries are skipped and reported instead.
    """
    parts = list(getattr(geom, "geoms", [geom]))
    if len(parts) != 1:
        return None
    coords = list(parts[0].coords)
    if len(coords) < 2:
        return None
    a = (round(coords[0][0], decimals), round(coords[0][1], decimals))
    b = (round(coords[-1][0], decimals), round(coords[-1][1], decimals))
    return a, b


def _assert_no_collapsed_features(
    frame: gpd.GeoDataFrame, decimals: int
) -> None:
    collapsed = frame.geometry.map(
        lambda geom: (
            (ends := _endpoints(geom, decimals)) is not None
            and ends[0] == ends[1]
        )
    )
    if collapsed.any():
        ids = frame.loc[collapsed, "id"].astype(int).tolist()[:5]
        raise NetworkError(
            f"{int(collapsed.sum())} topology pieces still collapse to one "
            f"published node after normalization (e.g. ids {ids})"
        )


def build_graph(edges: gpd.GeoDataFrame, params: Params) -> tuple[nx.Graph, dict, list]:
    """Build an undirected graph over ``edges`` (must be in EPSG:4326).

    Returns ``(graph, node_id_by_coord, per_edge_node_pairs)`` where the pair
    list is aligned with ``edges`` rows and holds ``(u, v)`` or ``None``.
    """
    decimals = int(params["meta.coord_decimals"])

    node_id: dict[tuple, int] = {}
    pairs: list[tuple[int, int] | None] = []
    graph = nx.Graph()
    skipped_multipart = 0
    skipped_loop = 0
    skipped_loop_m = 0.0

    lengths = io.to_working_crs(edges, params).geometry.length.values

    for i, geom in enumerate(edges.geometry.values):
        ends = _endpoints(geom, decimals)
        if ends is None:
            skipped_multipart += 1
            pairs.append(None)
            continue
        a, b = ends
        if a == b:
            # A closed loop connects nothing new; recording it as a self-edge
            # would only distort degree statistics.
            skipped_loop += 1
            skipped_loop_m += float(lengths[i])
            pairs.append(None)
            continue
        u = node_id.setdefault(a, len(node_id))
        v = node_id.setdefault(b, len(node_id))
        pairs.append((u, v))
        graph.add_edge(
            u, v,
            row=i,
            length_m=float(lengths[i]),
            lts=int(edges["lts"].values[i]),
        )

    log.info(
        "graph: %d nodes, %d edges (%d multipart and %d closed-loop features skipped)",
        graph.number_of_nodes(), graph.number_of_edges(), skipped_multipart, skipped_loop,
    )
    if skipped_multipart:
        log.warning(
            "%d features are multipart and carry no single endpoint pair; "
            "they are drawn but cannot participate in routing",
            skipped_multipart,
        )
    if skipped_loop:
        log.warning(
            "%d closed-loop features (%.1f m total) are drawn but cannot "
            "participate in routing; run "
            "split_for_connectors before build_graph",
            skipped_loop,
            skipped_loop_m,
        )
    return graph, node_id, pairs


# ---------------------------------------------------------------------------
#  Islands
# ---------------------------------------------------------------------------

def label_islands(
    edges: gpd.GeoDataFrame, pairs: list, params: Params
) -> tuple[gpd.GeoDataFrame, pd.DataFrame]:
    """Label connected components of the low-stress subnetwork.

    Islands are numbered by descending mileage, so island 0 is always the
    largest and the numbering is stable to read.
    """
    rules = lts_mod.Ruleset.from_params(params)
    min_segments = int(params["network.min_cluster_segments"])

    low = nx.Graph()
    metres = io.to_working_crs(edges, params).geometry.length.values

    for i, pair in enumerate(pairs):
        if pair is None:
            continue
        if not lts_mod.is_low_stress(int(edges["lts"].values[i]), rules):
            continue
        low.add_edge(pair[0], pair[1], row=i, length_m=float(metres[i]))

    components = list(nx.connected_components(low))

    # Rank by mileage rather than segment count: segment length spans two orders
    # of magnitude here, so counting segments would mis-rank the islands.
    scored = []
    for comp in components:
        rows = {
            low.edges[e]["row"]
            for e in low.edges(comp)
        }
        miles = sum(metres[r] for r in rows) / METRES_PER_MILE
        scored.append((miles, len(rows), rows))
    scored.sort(key=lambda t: (-t[0], -t[1]))

    # Label EVERY component, including two-segment stubs. The old script's
    # min_cluster_segments filter dropped 558 components here — 8.7% of
    # low-stress mileage — which would leave roughly one in eleven low-stress
    # clicks with no island to show. The threshold is kept as a *reporting*
    # cutoff (`major`) for top-N lists and barrier ranking, not as a labelling
    # filter, so the map can honestly say "an isolated two-block stretch".
    island_of_row: dict[int, int] = {}
    records = []
    for island_id, (miles, n_seg, rows) in enumerate(scored):
        for r in rows:
            island_of_row[r] = island_id
        records.append(
            {
                "island": island_id,
                "segments": n_seg,
                "miles": round(miles, 3),
                "major": bool(n_seg >= min_segments),
            }
        )

    out = edges.copy()
    out["island"] = [island_of_row.get(i, NO_ISLAND) for i in range(len(out))]

    islands = pd.DataFrame(records)
    total_low_mi = sum(m for m, _, _ in scored)
    largest = islands["miles"].iloc[0] if len(islands) else 0.0
    n_major = int(islands["major"].sum()) if len(islands) else 0
    minor_mi = islands.loc[~islands["major"], "miles"].sum() if len(islands) else 0.0

    log.info(
        "islands: %d components, %.1f low-stress miles; largest holds %.1f mi (%.1f%%)",
        len(components), total_low_mi, largest,
        100 * largest / total_low_mi if total_low_mi else 0.0,
    )
    log.info(
        "  %d are 'major' (>=%d segments); the %d smaller ones hold %.1f mi "
        "(%.1f%%) and are labelled but excluded from top-N lists and barrier ranking",
        n_major, min_segments, len(islands) - n_major, minor_mi,
        100 * minor_mi / total_low_mi if total_low_mi else 0.0,
    )
    return out, islands


# ---------------------------------------------------------------------------
#  Barriers
# ---------------------------------------------------------------------------

def rank_barriers(
    edges: gpd.GeoDataFrame, pairs: list, islands: pd.DataFrame, params: Params
) -> gpd.GeoDataFrame:
    """Rank high-stress segments that would bridge two low-stress islands.

    A *bridging* segment is one whose two endpoints already sit on different
    low-stress islands: treating it would merge them. Consecutive bridging
    segments sharing a road name and the same island pair are dissolved into one
    project, so a five-block corridor is reported once rather than five times.
    """
    rules = lts_mod.Ruleset.from_params(params)
    min_island_seg = int(params["network.barrier_min_island_segments"])

    if islands.empty:
        log.warning("no islands labelled; skipping barrier ranking")
        return gpd.GeoDataFrame(geometry=[], crs=edges.crs)

    miles_by_island = dict(zip(islands["island"], islands["miles"]))
    seg_by_island = dict(zip(islands["island"], islands["segments"]))

    # Which island does each low-stress node belong to?
    island_of_node: dict[int, int] = {}
    for i, pair in enumerate(pairs):
        if pair is None:
            continue
        isl = int(edges["island"].values[i])
        if isl == NO_ISLAND:
            continue
        island_of_node[pair[0]] = isl
        island_of_node[pair[1]] = isl

    metres = io.to_working_crs(edges, params).geometry.length.values
    candidates = []
    for i, pair in enumerate(pairs):
        if pair is None:
            continue
        if lts_mod.is_low_stress(int(edges["lts"].values[i]), rules):
            continue
        a = island_of_node.get(pair[0])
        b = island_of_node.get(pair[1])
        if a is None or b is None or a == b:
            continue
        if seg_by_island.get(a, 0) < min_island_seg or seg_by_island.get(b, 0) < min_island_seg:
            continue
        candidates.append(
            {
                "row": i,
                "name": edges["road_name"].values[i] if "road_name" in edges else None,
                "lts": int(edges["lts"].values[i]),
                "length_m": float(metres[i]),
                "pair": tuple(sorted((a, b))),
            }
        )

    log.info("barriers: %d bridging segments found", len(candidates))
    if not candidates:
        return gpd.GeoDataFrame(geometry=[], crs=edges.crs)

    # Dissolve into projects.
    projects: dict[tuple, dict] = {}
    for c in candidates:
        key = (c["name"], c["pair"])
        p = projects.setdefault(
            key,
            {
                "name": c["name"],
                "island_a": c["pair"][0],
                "island_b": c["pair"][1],
                "rows": [],
                "length_m": 0.0,
                "worst_lts": 0,
            },
        )
        p["rows"].append(c["row"])
        p["length_m"] += c["length_m"]
        p["worst_lts"] = max(p["worst_lts"], c["lts"])

    records = []
    for p in projects.values():
        a_mi = miles_by_island.get(p["island_a"], 0.0)
        b_mi = miles_by_island.get(p["island_b"], 0.0)
        # Score on the SMALLER island: merging a 40-mile island with a 0.2-mile
        # stub unlocks 0.2 miles of new reach, not 40. Divided by crossing length
        # because a short crossing is a cheaper intervention.
        unlocked = min(a_mi, b_mi)
        length_mi = p["length_m"] / METRES_PER_MILE
        score = unlocked / max(length_mi, 0.02)
        records.append(
            {
                "name": p["name"],
                "island_a": p["island_a"],
                "island_b": p["island_b"],
                "island_a_miles": round(a_mi, 2),
                "island_b_miles": round(b_mi, 2),
                "miles_unlocked": round(unlocked, 2),
                "crossing_miles": round(length_mi, 3),
                "current_lts": p["worst_lts"],
                "score": round(score, 2),
                "segment_ids": sorted(int(edges["id"].values[r]) for r in p["rows"]),
                "geometry": edges.geometry.values[p["rows"][0]],
            }
        )

    out = gpd.GeoDataFrame(records, geometry="geometry", crs=edges.crs)
    out = out.sort_values("score", ascending=False).reset_index(drop=True)
    out.insert(0, "rank", np.arange(1, len(out) + 1))

    # Several streets often cross between the SAME pair of islands, so treating
    # any one of them merges that pair. They are alternatives, not additive
    # wins: summing their "miles unlocked" would multiply-count the same
    # mileage. `best_for_pair` marks the cheapest crossing per island pair, and
    # `alternatives` says how many other options exist, so neither the UI nor a
    # council email can accidentally add them up.
    out["best_for_pair"] = ~out.duplicated(subset=["island_a", "island_b"], keep="first")
    pair_counts = out.groupby(["island_a", "island_b"])["rank"].transform("size")
    out["alternatives"] = (pair_counts - 1).astype("int32")

    distinct_pairs = int(out["best_for_pair"].sum())
    log.info(
        "barriers: dissolved into %d ranked projects across %d distinct island "
        "pairs (%d are alternative crossings of a pair already listed)",
        len(out), distinct_pairs, len(out) - distinct_pairs,
    )
    for _, r in out[out["best_for_pair"]].head(6).iterrows():
        log.info(
            "  #%d %-22s LTS %d, %.0f ft crossing, joins %.1f + %.1f mi "
            "(unlocks %.1f mi, %d alternative crossings)",
            r["rank"], str(r["name"])[:22], r["current_lts"],
            r["crossing_miles"] * 5280, r["island_a_miles"], r["island_b_miles"],
            r["miles_unlocked"], r["alternatives"],
        )
    return out

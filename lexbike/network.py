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

**Nodes are not endpoint-only.** A trail meeting a street mid-block used to
create no node at all, severing the network invisibly. Centrelines needing a
mid-block node for a trail connector are split there.

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
from shapely.geometry import LineString

from . import io
from . import lts as lts_mod
from .params import Params

log = logging.getLogger(__name__)

METRES_PER_MILE = 1609.344

#: Id space for the second and later pieces of a centreline split to host a
#: trail connector. The first piece keeps its parent SCLINK, so an existing deep
#: link still resolves to a real piece of the same street.
SPLIT_ID_BASE = 700_000_000

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
    """Split centrelines at the points where trail connectors attach.

    Without this, a connector's street end lands in the middle of a centreline
    where no node exists, and the trail stays invisibly disconnected — the
    mid-block T-junction problem.
    """
    if connectors.empty or "split_street_id" not in connectors.columns:
        return streets

    wanted: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for _, row in connectors.iterrows():
        street_id = int(row["split_street_id"])
        if street_id < 0:
            continue      # path-to-path connector; no centreline to split
        wanted[street_id].append((row["split_x"], row["split_y"]))

    st_m = io.to_working_crs(streets, params)
    by_id = {int(v): i for i, v in enumerate(streets["id"].values)}

    keep_rows: list[int] = []
    new_geoms: list[LineString] = []
    new_parent: list[int] = []
    split_count = 0

    for street_id, points in wanted.items():
        pos = by_id.get(street_id)
        if pos is None:
            continue
        line = st_m.geometry.values[pos]
        # Cut at every requested point, in along-line order.
        dists = sorted({line.project(_as_point(p)) for p in points})
        dists = [d for d in dists if 1e-6 < d < line.length - 1e-6]
        if not dists:
            continue
        pieces = cut_line(line, dists)
        if len(pieces) < 2:
            continue
        keep_rows.append(pos)
        new_geoms.extend(pieces)
        new_parent.extend([street_id] * len(pieces))
        split_count += 1

    if not keep_rows:
        log.info("no centreline needed splitting for connectors")
        return streets

    # Rebuild: replace each split parent with its pieces, inheriting attributes.
    frames = [streets.drop(streets.index[keep_rows])]
    rebuilt = []
    next_id = SPLIT_ID_BASE
    for parent_id, group in _group_pieces(new_parent, new_geoms):
        template = streets.iloc[by_id[parent_id]]
        for k, geom in enumerate(group):
            row = template.copy()
            row["geometry"] = geom
            if k > 0:
                row["id"] = next_id
                next_id += 1
            rebuilt.append(row)

    pieces_gdf = gpd.GeoDataFrame(rebuilt, crs=st_m.crs).to_crs(streets.crs)
    out = pd.concat([frames[0], pieces_gdf], ignore_index=True)
    out = gpd.GeoDataFrame(out, geometry="geometry", crs=streets.crs)

    log.info(
        "split %d centrelines into %d pieces so %d trail connectors land on real nodes",
        split_count, len(new_geoms), len(connectors),
    )
    return out


def _as_point(xy):
    from shapely.geometry import Point

    return Point(xy)


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


def _group_pieces(parents: list[int], geoms: list[LineString]):
    buckets: dict[int, list[LineString]] = defaultdict(list)
    for p, g in zip(parents, geoms):
        buckets[p].append(g)
    return buckets.items()


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

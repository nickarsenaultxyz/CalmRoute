"""Supplementary off-road paths from OpenStreetMap.

The county-scoped OSM query currently carries 70.0 miles of
``highway=cycleway`` and ``bicycle=designated`` path. After removing paths
already present in LFUCG, 21.0 miles remain -- including campus paths,
apartment-complex connectors and park links that the city file does not contain.

**Why this is importable when OSM streets are not.** Earlier work ruled out
mixing OSM into the pipeline, on the grounds that it would introduce a second
differently-noded network carrying none of the speed, volume or road-class
attributes the classifier needs. That reasoning holds for on-road facilities and
does not hold here: a path separated from traffic is LTS 1 *by definition*,
which is already exactly how LFUCG paths are rated. Nothing has to be inferred,
and nothing is conflated onto a centreline -- these are additional path
geometries attached by the same connector logic.

**What is deliberately excluded.** OSM also has 261 miles of paved
``path``/``footway`` with no bicycle tag. Almost all of it is sidewalk.
Importing it would inflate the network with footways, which is the kind of false
precision this project exists to avoid.

**Licensing.** OpenStreetMap data is ODbL. Anything published from it must carry
attribution, and a produced database mixing it in is likely subject to
share-alike. The public map supplies permanent attribution and leaves its
derived artifacts openly downloadable.
"""

from __future__ import annotations

import http.client
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import geopandas as gpd
from shapely.geometry import LineString
from shapely.ops import unary_union

from . import io
from .params import Params

log = logging.getLogger(__name__)

CACHE = Path(".cache/osm_cycleways.json")

#: Only ways a cyclist is actually invited onto. `highway=cycleway` and an
#: explicit `bicycle=designated` are the two unambiguous cases; everything else
#: (a paved footway, a desire line through a park) is left out.
QUERY = """
[out:json][timeout:{timeout}];
rel({relation_id});
map_to_area->.a;
(
  way(area.a)["highway"="cycleway"];
  way(area.a)["highway"~"^(path|footway)$"]["bicycle"="designated"];
);
out geom;
"""


class OsmError(Exception):
    """OSM data could not be obtained in a usable form."""


def fetch(params: Params, *, use_cache: bool = True) -> gpd.GeoDataFrame | None:
    """Download cycleways from Overpass, or read the local cache.

    Returns ``None`` only when the OSM layer is disabled. Once it is enabled,
    an unavailable or malformed response fails the build: silently publishing
    an LFUCG-only map would make a successful deployment misrepresent its data.
    """
    if not params.get("osm.enabled", False):
        return None

    if use_cache and CACHE.exists():
        log.info("osm paths: using cache %s", CACHE)
        return _parse(json.loads(CACHE.read_text()), params)

    endpoint = params["osm.overpass_url"]
    query = QUERY.format(
        timeout=int(params["osm.fetch_timeout_s"]),
        relation_id=int(params["osm.relation_id"]),
    )
    log.info("osm paths: querying %s", endpoint)
    attempts = int(params["osm.fetch_attempts"])
    payload = None
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            req = urllib.request.Request(
                endpoint,
                data=urllib.parse.urlencode({"data": query}).encode(),
                headers={
                    "User-Agent":
                        "lexbike-lts-build (+github.com/nickarsenaultxyz/Lex-Bike-Data)"
                },
            )
            with urllib.request.urlopen(
                req, timeout=int(params["osm.fetch_timeout_s"])
            ) as resp:
                payload = json.loads(resp.read())
            break
        except (
            http.client.IncompleteRead,
            urllib.error.URLError,
            TimeoutError,
            OSError,
            ValueError,
        ) as exc:
            last_error = exc
            if attempt < attempts:
                delay = attempt * 2
                log.warning(
                    "osm paths: attempt %d/%d failed (%s: %s); retrying in %ds",
                    attempt, attempts, type(exc).__name__, exc, delay,
                )
                time.sleep(delay)

    if payload is None:
        raise OsmError(
            f"OSM paths unavailable after {attempts} attempts "
            f"({type(last_error).__name__}: {last_error}); "
            "refusing to publish an LFUCG-only fallback"
        ) from last_error

    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(payload))
    return _parse(payload, params)


def _parse(payload: dict, params: Params) -> gpd.GeoDataFrame | None:
    rows = []
    for el in payload.get("elements", []):
        if el.get("type") != "way" or not el.get("geometry"):
            continue
        coords = [(p["lon"], p["lat"]) for p in el["geometry"]]
        if len(coords) < 2:
            continue
        tags = el.get("tags", {})
        rows.append({
            "osm_id": el.get("id"),
            "name": tags.get("name") or tags.get("ref") or "",
            "osm_kind": "cycleway" if tags.get("highway") == "cycleway"
                        else "designated path",
            "surface": tags.get("surface", ""),
            "geometry": LineString(coords),
        })
    if not rows:
        raise OsmError("OSM path query returned nothing usable")

    gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs=4326)
    log.info(
        "osm paths: %d ways, %.1f mi before de-duplication",
        len(gdf), io.to_working_crs(gdf, params).geometry.length.sum() / 1609.344,
    )
    return gdf


def dedupe(osm: gpd.GeoDataFrame, existing: gpd.GeoDataFrame,
           params: Params) -> gpd.GeoDataFrame:
    """Drop OSM ways that duplicate a path LFUCG already has.

    Roughly a quarter of OSM's cycleway mileage is the Legacy Trail, Town Branch
    and friends, which are already in the city layer. Importing both would draw
    every major trail twice and double-count it in every statistic.

    A way is dropped when most of its length lies inside a buffer around the
    existing off-road network -- length-based rather than endpoint-based, so a
    trail digitized with different vertices still matches.
    """
    if osm is None or osm.empty:
        return osm
    buffer_m = float(params["osm.dedupe_buffer_m"])
    max_overlap = float(params["osm.dedupe_max_overlap"])

    osm_m = io.to_working_crs(osm, params).reset_index(drop=True)
    have = io.to_working_crs(existing, params)
    off = have[~have["on_road"]] if "on_road" in have.columns else have
    if off.empty:
        return osm

    corridor = unary_union(off.geometry.buffer(buffer_m).values)
    keep = []
    for i, geom in enumerate(osm_m.geometry):
        if geom.length <= 0:
            continue
        share = geom.intersection(corridor).length / geom.length
        if share < max_overlap:
            keep.append(i)

    out = osm.iloc[keep].reset_index(drop=True)
    dropped_mi = (osm_m.geometry.length.sum()
                  - io.to_working_crs(out, params).geometry.length.sum()) / 1609.344
    log.info(
        "osm paths: kept %d of %d ways (%.1f mi dropped as duplicating LFUCG)",
        len(out), len(osm), dropped_mi,
    )
    return out


def as_facilities(osm: gpd.GeoDataFrame, params: Params) -> gpd.GeoDataFrame:
    """Shape OSM ways like rows of the LFUCG bike layer.

    Downstream code takes one facility frame, so rather than special-casing OSM
    everywhere it is given the same columns: an off-road, existing, path-class
    facility. ``source`` marks the provenance so the map can filter or label it
    and no figure has to guess where a segment came from.
    """
    if osm is None or osm.empty:
        return osm
    out = osm.copy()
    out["fac"] = "path"
    out["on_road"] = False
    out["status"] = params["scenario.existing_status"]
    out["network_name"] = out["name"]
    out["facility_name"] = out["osm_kind"]
    out["recommended"] = ""
    out["source"] = "osm"
    # Distinct id space so an OSM way can never collide with an SCLINK.
    out["id_src"] = 600_000_000 + out.index.to_numpy()
    return out


def quality_gate(kept: gpd.GeoDataFrame, params: Params) -> None:
    """Refuse an import that looks like a bad Overpass day.

    Without this a malformed query or an OSM edit war could silently add or
    remove tens of miles of "bike network" between two builds.
    """
    if kept is None or kept.empty:
        raise OsmError("OSM import produced no usable paths")
    miles = io.to_working_crs(kept, params).geometry.length.sum() / 1609.344
    lo = float(params["osm.expect_min_miles"])
    hi = float(params["osm.expect_max_miles"])
    log.info("osm paths: %.1f mi net new", miles)
    if not (lo <= miles <= hi):
        raise OsmError(
            f"OSM import is {miles:.1f} mi, outside the expected {lo:.0f}-{hi:.0f} mi. "
            "Either OSM has changed materially or the query is wrong; review "
            "before publishing."
        )

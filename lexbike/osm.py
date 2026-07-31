"""Supplementary bicycle routing links from OpenStreetMap.

The county-scoped OSM query currently carries 70.0 miles of
``highway=cycleway`` and ``bicycle=designated`` path. After removing paths
already present in LFUCG, 21.0 miles remain -- including campus paths,
apartment-complex connectors and park links that the city file does not contain.

**Why this is importable when the general OSM street network is not.** A path
separated from traffic is LTS 1 *by definition*, which is already how LFUCG
paths are rated. Two reviewed street exceptions are different: the Baptist
service-road corridor is included because OSM explicitly permits bicycles and
is rated LTS 2; Commonwealth Drive is a locally reviewed 25 mph residential
corridor and is rated LTS 1. Neither is counted as a bike facility. These
geometries use the same attachment logic as LFUCG paths rather than creating a
parallel street network.

**What is deliberately excluded.** OSM also has 261 miles of paved
``path``/``footway`` with no bicycle tag. Almost all of it is sidewalk.
Importing it would inflate the network with footways, which is the kind of false
precision this project exists to avoid. Generic, private, parking and one-way
service roads are excluded too.

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

CACHE = Path(".cache/osm_bike_network_v2.json")

#: The general query admits only ways a cyclist is explicitly invited onto. The
#: two named-street clauses are narrow reviewed exceptions for geometry missing
#: from LFUCG: Commonwealth Drive and the short University Court approach that
#: joins its west end to the existing street graph.
QUERY = """
[out:json][timeout:{timeout}];
rel({relation_id});
map_to_area->.a;
(
  way(area.a)["highway"="cycleway"];
  way(area.a)["highway"~"^(path|footway)$"]["bicycle"="designated"];
)->.paths;
way(area.a)["highway"="service"]["bicycle"~"^(yes|designated|permissive)$"]
  ["name"="{access_seed_name}"]->.access_seed;
way(around.access_seed:{access_search_radius_m})["highway"="service"]
  ["bicycle"~"^(yes|designated|permissive)$"]->.access_near;
way(area.a)["highway"~"^(residential|unclassified|service)$"]
  ["name"="{reviewed_street_name}"]->.reviewed_street;
node(w.reviewed_street)->.reviewed_street_nodes;
way(bn.reviewed_street_nodes)["highway"~"^(residential|unclassified|service)$"]
  ["name"="{reviewed_connector_name}"]->.reviewed_street_connector;
(.paths;.access_seed;.access_near;.reviewed_street;.reviewed_street_connector;);
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
        access_seed_name=_overpass_string(
            str(params["osm.required_access_names"][0])
        ),
        access_search_radius_m=int(params["osm.access_search_radius_m"]),
        reviewed_street_name=_overpass_string(
            str(params["osm.required_reviewed_street_names"][0])
        ),
        reviewed_connector_name=_overpass_string(
            str(params["osm.reviewed_street_connector_names"][0])
        ),
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
    reviewed_street_names = set(
        params.get("osm.required_reviewed_street_names", [])
    )
    reviewed_street_names.update(
        params.get("osm.reviewed_street_connector_names", [])
    )
    for el in payload.get("elements", []):
        if el.get("type") != "way" or not el.get("geometry"):
            continue
        coords = [(p["lon"], p["lat"]) for p in el["geometry"]]
        if len(coords) < 2:
            continue
        tags = el.get("tags", {})
        role = _role(tags, reviewed_street_names)
        if role is None:
            continue
        rows.append({
            "osm_id": el.get("id"),
            "name": tags.get("name") or tags.get("ref") or "",
            "osm_role": role,
            "osm_kind": (
                "bicycle-access service road" if role == "access"
                else "reviewed low-stress street" if role == "reviewed_street"
                else "cycleway" if tags.get("highway") == "cycleway"
                else "designated path"
            ),
            "surface": tags.get("surface", ""),
            "_nodes": tuple(el.get("nodes", [])),
            "geometry": LineString(coords),
        })
    if not rows:
        raise OsmError("OSM path query returned nothing usable")

    keep_access = _seeded_access_indices(
        rows, set(params.get("osm.required_access_names", []))
    )
    rows = [
        row for i, row in enumerate(rows)
        if row["osm_role"] in {"path", "reviewed_street"} or i in keep_access
    ]
    gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs=4326)
    gdf = gdf.drop(columns="_nodes")
    log.info(
        "osm paths: %d ways, %.1f mi before de-duplication",
        len(gdf), io.to_working_crs(gdf, params).geometry.length.sum() / 1609.344,
    )
    return gdf


def _overpass_string(value: str) -> str:
    """Escape a controlled string for an Overpass quoted literal."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _seeded_access_indices(rows: list[dict], seed_names: set[str]) -> set[int]:
    """Keep complete access-road components containing a reviewed named seed.

    The useful Baptist corridor contains one named entrance and several unnamed
    continuation ways. Expanding by shared OSM node retains that whole corridor
    without admitting 1,800 unrelated service roads elsewhere in the county.
    """
    access = [i for i, row in enumerate(rows) if row["osm_role"] == "access"]
    by_node: dict[int, list[int]] = {}
    for i in access:
        for node in rows[i].get("_nodes", ()):
            by_node.setdefault(int(node), []).append(i)

    seen = {
        i for i in access
        if str(rows[i].get("name") or "").strip() in seed_names
    }
    frontier = list(seen)
    while frontier:
        i = frontier.pop()
        for node in rows[i].get("_nodes", ()):
            for neighbour in by_node.get(int(node), ()):
                if neighbour not in seen:
                    seen.add(neighbour)
                    frontier.append(neighbour)
    return seen


def _role(tags: dict, reviewed_street_names: set[str] | None = None) -> str | None:
    """Classify one OSM way, applying the narrow supplement policies.

    A configured reviewed street is accepted by name and permitted road class.
    Otherwise a service road is usable only when bicycle access is explicit.
    Parking aisles and private roads are never generic shortcuts. One-way
    service roads are held back until the browser graph can represent direction;
    treating one as undirected would create an illegal route in one direction.
    """
    highway = tags.get("highway")
    bicycle = tags.get("bicycle")
    name = str(tags.get("name") or "").strip()
    if (
        name in (reviewed_street_names or set())
        and highway in {"residential", "unclassified", "service"}
        and tags.get("access") not in {"private", "no"}
        and tags.get("service") != "parking_aisle"
    ):
        return "reviewed_street"
    if highway == "cycleway":
        return "path"
    if highway in {"path", "footway"} and bicycle == "designated":
        return "path"
    if highway != "service" or bicycle not in {"yes", "designated", "permissive"}:
        return None
    if "parking" in name.casefold():
        return None
    if tags.get("service") == "parking_aisle":
        return None
    if tags.get("access") in {"private", "no"}:
        return None
    if tags.get("oneway") in {"yes", "1", "-1"}:
        return None
    return "access"


def dedupe(osm: gpd.GeoDataFrame, existing: gpd.GeoDataFrame,
           params: Params, streets: gpd.GeoDataFrame | None = None) -> gpd.GeoDataFrame:
    """Drop OSM ways that duplicate geometry LFUCG already has.

    Paths are compared with LFUCG's off-road facilities. Access roads and
    reviewed streets are compared with LFUCG street centrelines, which prevents
    an OSM street from being added twice when the city already carries it.

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
    path_corridor = (
        unary_union(off.geometry.buffer(buffer_m).values)
        if not off.empty else None
    )
    access_corridor = None
    if streets is not None and len(streets):
        access_buffer = float(params["osm.access_dedupe_buffer_m"])
        access_corridor = unary_union(
            io.to_working_crs(streets, params).geometry.buffer(access_buffer).values
        )

    keep = []
    for i, geom in enumerate(osm_m.geometry):
        if geom.length <= 0:
            continue
        role = osm_m.iloc[i].get("osm_role", "path")
        corridor = (
            access_corridor
            if role in {"access", "reviewed_street"}
            else path_corridor
        )
        if corridor is None:
            keep.append(i)
            continue
        share = geom.intersection(corridor).length / geom.length
        if share < max_overlap:
            keep.append(i)

    out = osm.iloc[keep].reset_index(drop=True)
    dropped_mi = (osm_m.geometry.length.sum()
                  - io.to_working_crs(out, params).geometry.length.sum()) / 1609.344
    log.info(
        "osm supplement: kept %d of %d ways (%.1f mi dropped as duplicating LFUCG)",
        len(out), len(osm), dropped_mi,
    )
    return out


def as_facilities(osm: gpd.GeoDataFrame, params: Params) -> gpd.GeoDataFrame:
    """Shape OSM ways like rows of the LFUCG bike layer.

    Downstream code takes one facility frame, so rather than special-casing OSM
    everywhere it is given the same columns. Dedicated paths retain the path
    facility type. Bicycle-access service roads and reviewed missing streets
    carry no bike-facility credit; they receive their reviewed ratings later in
    ``assemble_edges``. ``source`` marks provenance so the map can label and
    audit the result.
    """
    if osm is None or osm.empty:
        return osm
    out = osm.copy()
    out["fac"] = out["osm_role"].map({
        "path": "path",
        "access": "none",
        "reviewed_street": "none",
    })
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
    frame = io.to_working_crs(kept, params)
    miles = frame.geometry.length / 1609.344
    for role, label, lo_key, hi_key in [
        ("path", "paths", "osm.expect_min_miles", "osm.expect_max_miles"),
        (
            "access", "bike-access roads",
            "osm.expect_min_access_miles", "osm.expect_max_access_miles",
        ),
        (
            "reviewed_street", "reviewed missing streets",
            "osm.expect_min_reviewed_street_miles",
            "osm.expect_max_reviewed_street_miles",
        ),
    ]:
        hit = kept["osm_role"] == role
        value = float(miles[hit].sum())
        lo = float(params[lo_key])
        hi = float(params[hi_key])
        log.info("osm %s: %.1f mi net new", label, value)
        if not (lo <= value <= hi):
            raise OsmError(
                f"OSM {label} import is {value:.1f} mi, outside the expected "
                f"{lo:.1f}-{hi:.1f} mi. Either OSM has changed materially or "
                "the query is wrong; review before publishing."
            )

    names = set(kept.loc[kept["osm_role"] == "access", "name"])
    missing = set(params.get("osm.required_access_names", [])) - names
    if missing:
        raise OsmError(
            "required bicycle-access road(s) missing after de-duplication: "
            + ", ".join(sorted(missing))
        )

    reviewed_names = set(
        kept.loc[kept["osm_role"] == "reviewed_street", "name"]
    )
    missing_reviewed = (
        set(params.get("osm.required_reviewed_street_names", []))
        - reviewed_names
    )
    if missing_reviewed:
        raise OsmError(
            "required reviewed street(s) missing after de-duplication: "
            + ", ".join(sorted(missing_reviewed))
        )

"""Council districts, so "email a council member" reaches the right one.

Which council member represents a street depends on where the street is, so
this attaches a district to every segment and publishes the roster alongside.

The roster is read from LFUCG's own published directory at build time rather
than copied into this repository, for one substantive reason: council members
change with elections. A hardcoded name and address would keep working long
after it had become wrong, and quietly send constituent mail to someone who no
longer holds the seat. Reading the authoritative source on every deploy means
the map is as current as the city's own data.

That does introduce a build-time network dependency, so a failed fetch degrades
rather than breaking: segments simply carry no district, and the UI falls back
to the council's contact page.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from pathlib import Path

import geopandas as gpd
import pandas as pd

from .params import Params

log = logging.getLogger(__name__)

CACHE = Path(".cache/council_districts.geojson")


def fetch(params: Params, *, use_cache: bool = True) -> gpd.GeoDataFrame | None:
    """Download the council district layer, or read a local cache.

    Returns ``None`` if the layer cannot be obtained, which callers must treat
    as "this build has no district data" rather than as an error.
    """
    url = params["council.source_url"]
    timeout = int(params["council.fetch_timeout_s"])

    if use_cache and CACHE.exists():
        log.info("council districts: using cache %s", CACHE)
        return _load(CACHE)

    log.info("council districts: fetching %s", url)
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "lexbike-lts-build (+github.com/nickarsenaultxyz/Lex-Bike-Data)"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        log.warning(
            "council districts unavailable (%s: %s). The map will fall back to "
            "the council's general contact page.", type(exc).__name__, exc,
        )
        return None

    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_bytes(payload)
    return _load(CACHE)


def _load(path: Path) -> gpd.GeoDataFrame | None:
    try:
        gdf = gpd.read_file(path)
    except Exception as exc:
        log.warning("council district layer could not be read (%s)", exc)
        return None
    if gdf.empty or "DISTRICT" not in gdf.columns:
        log.warning("council district layer has no DISTRICT column; ignoring")
        return None
    # Published in Kentucky State Plane North (EPSG:2246); everything here
    # works in WGS84.
    return gdf.to_crs(4326)


def attach(edges: gpd.GeoDataFrame, districts: gpd.GeoDataFrame | None,
           params: Params) -> gpd.GeoDataFrame:
    """Add a ``council`` column: the district a segment sits in.

    Uses each segment's representative point rather than its full extent. A
    street on a district boundary belongs to whichever district contains its
    midpoint, which is a defensible single answer; a segment genuinely spanning
    two districts is a case for contacting both, and the UI says so.
    """
    out = edges.copy()
    out["council"] = pd.NA
    if districts is None or districts.empty:
        return out

    pts = out.geometry.representative_point()
    probe = gpd.GeoDataFrame({"__row": range(len(out))}, geometry=pts, crs=out.crs)
    joined = gpd.sjoin(
        probe, districts[["DISTRICT", "geometry"]], how="left", predicate="within"
    )
    # sjoin can emit several rows when polygons overlap at a shared edge; the
    # first is enough and keeps the result aligned with `edges`.
    joined = joined.drop_duplicates(subset="__row", keep="first").sort_values("__row")
    # Int16 is a pandas nullable dtype, so it has to be applied to a Series --
    # numpy does not know it. Districts are absent for anything outside the
    # urban county boundary, hence nullable rather than int.
    out["council"] = pd.Series(
        pd.to_numeric(joined["DISTRICT"].values, errors="coerce"), index=out.index
    ).astype("Int16")

    matched = int(out["council"].notna().sum())
    log.info(
        "council districts: matched %d / %d segments (%d unmatched, typically "
        "just outside the urban county boundary)",
        matched, len(out), len(out) - matched,
    )
    return out


def roster(districts: gpd.GeoDataFrame | None) -> dict:
    """District number -> the published contact details for that seat.

    This is a government directory of elected officials in their official
    capacity, republished so a constituent can reach the right representative
    about a specific street. It is regenerated on every build from the city's
    own layer, so it cannot go stale in this repository.
    """
    if districts is None or districts.empty:
        return {}

    out: dict[str, dict] = {}
    for _, row in districts.iterrows():
        num = pd.to_numeric(row.get("DISTRICT"), errors="coerce")
        if pd.isna(num):
            continue
        entry = {
            "name": _clean(row.get("REP")),
            "email": _clean(row.get("EMAIL")),
            "phone": _clean(row.get("TELEPHONE")),
            "url": _clean(row.get("URL")),
        }
        out[str(int(num))] = {k: v for k, v in entry.items() if v}
    return out


def _clean(value) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()

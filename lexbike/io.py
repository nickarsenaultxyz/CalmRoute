"""Source loading, normalization, and validation.

Everything that touches disk or reconciles quirks in the LFUCG/KYTC exports
lives here, so the classifier in :mod:`lexbike.lts` can stay pure.
"""

from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
import pandas as pd

from .params import Params

gpd.options.io_engine = "pyogrio"

log = logging.getLogger(__name__)

BIKE_PATH = Path("lexbike.geojson")
STREET_PATH = Path("lex_street_data.geojson")
AADT_PATH = Path("StaList_Fayette (1).csv")

# SCLINK is unique and non-null across all 13,775 centrelines, so it is the
# stable feature id: deep links into the map survive a data refresh.
ID_COL = "SCLINK"


class SourceDataError(Exception):
    """A source file is missing or violates an assumption we rely on."""


def _require(path: Path) -> Path:
    if not path.exists():
        raise SourceDataError(
            f"missing source file: {path}\n"
            "Expected the LFUCG/KYTC exports in the repository root."
        )
    return path


def _norm_text(s: pd.Series) -> pd.Series:
    """Collapse whitespace so ``'EXISTING BUFFERED BIKE LANE '`` and its
    un-padded twin do not read as two different values."""
    return s.astype("string").str.strip().str.replace(r"\s+", " ", regex=True)


def load_streets(params: Params, path: Path = STREET_PATH) -> gpd.GeoDataFrame:
    """Load street centrelines, the canonical network."""
    gdf = gpd.read_file(_require(path))
    gdf = _ensure_4326(gdf)
    log.info("loaded %d street centrelines from %s", len(gdf), path)

    if ID_COL not in gdf.columns:
        raise SourceDataError(f"{path}: expected an {ID_COL} column")
    if gdf[ID_COL].isna().any():
        raise SourceDataError(f"{path}: {ID_COL} has nulls; cannot use it as a feature id")
    if not gdf[ID_COL].is_unique:
        dupes = gdf[ID_COL][gdf[ID_COL].duplicated()].unique()[:5]
        raise SourceDataError(
            f"{path}: {ID_COL} is not unique (e.g. {list(dupes)}). "
            "Feature ids must be stable and unique for deep links to work."
        )
    gdf["id"] = gdf[ID_COL].astype("int64")

    # RDCLASS typo repair. One row is 69 (W HICKMAN PLANT RD); left alone it is
    # silently dropped by every isin() filter downstream, which is how the old
    # pipeline lost it.
    typo_fixes = {int(k): int(v) for k, v in params["rdclass.typo_fixes"].items()}
    rdclass = pd.to_numeric(gdf["RDCLASS"], errors="coerce")
    for bad, good in typo_fixes.items():
        hit = rdclass == bad
        if hit.any():
            names = gdf.loc[hit, "ROADNAME"].fillna("<unnamed>").tolist()
            log.warning(
                "RDCLASS %d is not a valid class; treating as %d for %d segment(s): %s",
                bad, good, int(hit.sum()), ", ".join(names[:5]),
            )
            rdclass = rdclass.where(~hit, good)

    known = set(params["rdclass.labels"].keys())
    unknown = set(rdclass.dropna().astype(int).astype(str)) - known
    if unknown:
        raise SourceDataError(
            f"{path}: unmapped RDCLASS value(s) {sorted(unknown)}. "
            "Add them to [rdclass.labels] (and [rdclass.typo_fixes] if they are errors)."
        )
    gdf["rdclass"] = rdclass.astype("int16")

    gdf["speed_mph"] = pd.to_numeric(gdf["SPEED"], errors="coerce")
    if gdf["speed_mph"].isna().any():
        # Currently zero, but a future export could regress; imputing silently
        # is what made the old pipeline rate 62 facilities LTS 4 by accident.
        log.warning(
            "%d centreline(s) have no SPEED; they will be rated from class alone",
            int(gdf["speed_mph"].isna().sum()),
        )

    gdf["cartoclass"] = pd.to_numeric(gdf["CartoClass"], errors="coerce").astype("Int16")
    gdf["road_name"] = _norm_text(gdf["ROADNAME"])
    # MAINTENANCE has 37 rows holding a single space; make that a real null so
    # it groups with the other unknowns during AADT imputation.
    gdf["maintenance"] = _norm_text(gdf["MAINTENANCE"]).replace("", pd.NA)
    # 'B' = both directions. 'FT'/'TF' are directional.
    gdf["oneway"] = _norm_text(gdf["ONEWAY"]).fillna("B")
    gdf["directional"] = gdf["oneway"] != "B"

    return gdf


def load_bike_facilities(params: Params, path: Path = BIKE_PATH) -> gpd.GeoDataFrame:
    """Load the bike facility overlay.

    Only ``Type_Facility`` determines the rating. See the long comment in
    ``params.toml`` under ``[facility]``: ``AltType_Facility`` is a recommended
    upgrade, not existing infrastructure, and ``Name_Facility`` is a
    Status-prefixed restatement of ``Type_Facility``.
    """
    gdf = gpd.read_file(_require(path))
    gdf = _ensure_4326(gdf)
    log.info("loaded %d bike facility segments from %s", len(gdf), path)

    mapping = params["facility.type_map"]
    raw = _norm_text(gdf["Type_Facility"])

    unknown = sorted(set(raw.dropna().unique()) - set(mapping.keys()))
    if unknown:
        raise SourceDataError(
            f"{path}: unmapped Type_Facility value(s): {unknown}\n"
            "Add each one to [facility.type_map] in params.toml. Failing loudly here is "
            "deliberate: silently bucketing an unknown facility as 'none' is how "
            "219 'Preferred Route' segments were previously misrated."
        )
    if raw.isna().any():
        log.warning(
            "%d facility segment(s) have no Type_Facility; treated as no facility",
            int(raw.isna().sum()),
        )
    gdf["fac"] = raw.map(mapping).fillna("none")

    # Source-layer id, used to record which facility credited a centreline so a
    # rating can be traced back to the record that justified it.
    if "OBJECTID" in gdf.columns and gdf["OBJECTID"].is_unique:
        gdf["id_src"] = pd.to_numeric(gdf["OBJECTID"], errors="coerce").astype("int64")
    else:
        gdf["id_src"] = pd.RangeIndex(len(gdf)).astype("int64")

    gdf["status"] = _norm_text(gdf["Status"])
    gdf["on_road"] = _norm_text(gdf["Type_Road"]).eq("On Road")
    gdf["recommended"] = _norm_text(gdf.get("AltType_Facility"))
    gdf["facility_name"] = _norm_text(gdf.get("Name_Facility"))
    gdf["network_name"] = _norm_text(gdf.get("Name_Network"))

    existing = params["scenario.existing_status"]
    funded = params["scenario.funded_status"]
    counts = gdf["status"].value_counts(dropna=False).to_dict()
    log.info("facility Status breakdown: %s", counts)
    if existing not in counts:
        raise SourceDataError(
            f"{path}: no segment has Status == {existing!r}; "
            "check [scenario] in params.toml against the source data."
        )

    # Cross-check Status against the Name_Facility prefix. They should agree;
    # a disagreement means one of the two is stale and ratings could include
    # unbuilt infrastructure.
    name_says_funded = gdf["facility_name"].str.startswith("FUNDED", na=False)
    status_says_funded = gdf["status"].eq(funded)
    conflict = int((name_says_funded != status_says_funded).sum())
    if conflict:
        log.warning(
            "%d segment(s) disagree between Status and the Name_Facility prefix; "
            "Status is treated as authoritative",
            conflict,
        )

    return gdf


def load_aadt(params: Params, path: Path = AADT_PATH) -> pd.Series:
    """Median AADT per KYTC count station, keyed for joining onto ``KYDOT``.

    Keys on the last N characters of ``Sta ID`` rather than stripping a
    hardcoded ``034`` prefix, which silently dropped the records prefixed
    105/057/025 and would have produced zero matches for any other county.
    """
    if not path.exists():
        log.warning("no AADT file at %s; volumes will be imputed for every segment", path)
        return pd.Series(dtype="float64", name="aadt")

    df = pd.read_csv(path)
    df["AADT"] = pd.to_numeric(df["AADT"], errors="coerce")
    before = len(df)
    df = df.dropna(subset=["AADT"])

    n = int(params["aadt.station_key_chars"])
    df["__key"] = df["Sta ID"].astype("string").str.strip().str[-n:]

    by_station = df.groupby("__key")["AADT"].median()
    years = pd.to_numeric(df.get("Year"), errors="coerce").dropna()
    log.info(
        "AADT: %d usable records of %d -> %d station keys; count years %d-%d (median %d)",
        len(df), before, len(by_station),
        int(years.min()) if len(years) else 0,
        int(years.max()) if len(years) else 0,
        int(years.median()) if len(years) else 0,
    )
    return by_station.rename("aadt")


#: ``aadt_src`` codes, published in methodology.json.
AADT_STATION = 0
AADT_IMPUTED_NARROW = 1
AADT_IMPUTED_COARSE = 2


def attach_aadt(
    streets: gpd.GeoDataFrame, by_station: pd.Series, params: Params
) -> gpd.GeoDataFrame:
    """Resolve a traffic volume for every segment, recording how.

    Two-part policy replacing the old code's per-branch improvisation:

    1. Join real counts on the KYDOT route key.
    2. Impute the rest from group medians, cascading to progressively coarser
       keys, so no segment reaches the classifier with an unresolved volume.

    ``aadt_src`` records which happened, and it drives the confidence field and
    the "this is an estimate" note in the UI. The distinction matters: KYTC only
    counts state-maintained routes, so an imputed value on a local street is
    drawn from the busiest third of its class and is systematically high.
    """
    gdf = streets.copy()
    gdf["aadt"] = pd.NA
    gdf["aadt_src"] = pd.NA

    if len(by_station) and "KYDOT" in gdf.columns:
        key = _norm_text(gdf["KYDOT"])
        matched = key.map(by_station)
        gdf["aadt"] = matched
        gdf.loc[matched.notna(), "aadt_src"] = AADT_STATION
        log.info(
            "AADT: matched %d / %d segments to a count station via KYDOT",
            int(matched.notna().sum()), len(gdf),
        )

    groups: list[list[str]] = [list(g) for g in params["aadt.impute_groups"]]
    floor = float(params["aadt.impute_floor"])

    for depth, keys in enumerate(groups):
        missing = gdf["aadt"].isna()
        if not missing.any():
            break
        usable = [k for k in keys if k in gdf.columns]
        if len(usable) != len(keys):
            log.warning("AADT imputation: skipping group %s (missing columns)", keys)
            continue
        # Medians come only from real counts, never from earlier imputations,
        # so a coarse fallback cannot be contaminated by a narrow one.
        real = gdf[gdf["aadt_src"] == AADT_STATION]
        if real.empty:
            break
        medians = real.groupby(usable, dropna=False)["aadt"].median()
        filled = gdf.loc[missing].set_index(usable).index.map(medians)
        gdf.loc[missing, "aadt"] = pd.Series(filled, index=gdf.index[missing], dtype="float64")
        now_filled = missing & gdf["aadt"].notna()
        gdf.loc[now_filled, "aadt_src"] = (
            AADT_IMPUTED_NARROW if depth == 0 else AADT_IMPUTED_COARSE
        )
        log.info(
            "AADT: imputed %d segments from %s medians",
            int(now_filled.sum()), "+".join(usable),
        )

    still_missing = gdf["aadt"].isna()
    if still_missing.any():
        gdf.loc[still_missing, "aadt"] = floor
        gdf.loc[still_missing, "aadt_src"] = AADT_IMPUTED_COARSE
        log.info("AADT: %d segments fell back to the floor (%d)", int(still_missing.sum()), floor)

    gdf["aadt"] = pd.to_numeric(gdf["aadt"], errors="coerce").astype("float64")
    gdf["aadt_src"] = gdf["aadt_src"].astype("int8")

    breakdown = gdf["aadt_src"].value_counts().sort_index().to_dict()
    log.info(
        "AADT provenance: station=%d narrow=%d coarse=%d",
        breakdown.get(AADT_STATION, 0),
        breakdown.get(AADT_IMPUTED_NARROW, 0),
        breakdown.get(AADT_IMPUTED_COARSE, 0),
    )
    return gdf


def aadt_years(path: Path = AADT_PATH) -> dict[str, int]:
    """Count-year range, published so the map can date its traffic figures."""
    if not path.exists():
        return {}
    years = pd.to_numeric(pd.read_csv(path).get("Year"), errors="coerce").dropna()
    if years.empty:
        return {}
    return {
        "min": int(years.min()),
        "max": int(years.max()),
        "median": int(years.median()),
    }


def _ensure_4326(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    return gdf.set_crs(4326) if gdf.crs is None else gdf.to_crs(4326)


def to_working_crs(gdf: gpd.GeoDataFrame, params: Params) -> gpd.GeoDataFrame:
    """Reproject to the metre-based working CRS (UTM 16N)."""
    return gdf.to_crs(int(params["meta.crs_working"]))

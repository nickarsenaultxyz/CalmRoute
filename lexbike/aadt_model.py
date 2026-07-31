"""Validated AADT prediction for streets represented by the count programme.

KYTC counts are not a random sample: they favour larger, state-maintained
roads. A flexible model trained on them can improve predictions for comparable
roads, but applying it to neighbourhood streets would learn that selection
bias rather than local traffic. This module therefore returns both predictions
and a support mask. The caller keeps the existing median cascade wherever a
road class or categorical value is outside the reviewed training domain.

The target is log(AADT), which keeps a handful of interstate counts from
dominating the fit. Repeated centreline pieces sharing one station key receive
weights summing to one, so a heavily segmented corridor is not mistaken for
dozens of independent observations.
"""

from __future__ import annotations

from dataclasses import dataclass

import geopandas as gpd
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.metrics import mean_absolute_error, mean_squared_log_error
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OrdinalEncoder

from .params import Params


_MISSING = "__missing__"
_CATEGORY_SOURCES = {
    "rdclass_cat": "rdclass",
    "cartoclass_cat": "cartoclass",
    "maintenance_cat": "maintenance",
    "oneway_cat": "oneway",
    "snow": "SNOW",
    "sweep": "SWEEP",
    "zip": "ZIP_LEFT",
}
_CATEGORICAL = list(_CATEGORY_SOURCES)
_NUMERIC = ["speed_mph", "directional", "x_km", "y_km", "length_km"]


@dataclass(frozen=True)
class Prediction:
    """Model output aligned to the source street index."""

    values: pd.Series
    supported: pd.Series
    station_keys: int


def _text(values: pd.Series) -> pd.Series:
    return (
        values.astype("string")
        .fillna(_MISSING)
        .astype(str)
        .str.strip()
        .replace("", _MISSING)
    )


def _source_column(streets: gpd.GeoDataFrame, name: str) -> pd.Series:
    if name in streets.columns:
        return streets[name]
    return pd.Series(pd.NA, index=streets.index, dtype="object")


def feature_frame(
    streets: gpd.GeoDataFrame, params: Params
) -> pd.DataFrame:
    """Build deterministic model features without mutating the source frame."""
    out = pd.DataFrame(index=streets.index)
    for destination, source in _CATEGORY_SOURCES.items():
        out[destination] = _text(_source_column(streets, source))

    working = streets.to_crs(int(params["meta.crs_working"]))
    centroids = working.geometry.centroid
    out["speed_mph"] = pd.to_numeric(
        _source_column(streets, "speed_mph"), errors="coerce"
    )
    out["directional"] = (
        _source_column(streets, "directional").fillna(False).astype(bool).astype(int)
    )
    out["x_km"] = centroids.x / 1000.0
    out["y_km"] = centroids.y / 1000.0
    out["length_km"] = working.geometry.length / 1000.0
    return out


def _estimator(params: Params) -> Pipeline:
    model = params["aadt.model"]
    preprocessing = ColumnTransformer(
        [
            (
                "categorical",
                OrdinalEncoder(
                    handle_unknown="use_encoded_value",
                    unknown_value=-1,
                ),
                _CATEGORICAL,
            ),
            ("numeric", SimpleImputer(strategy="median"), _NUMERIC),
        ]
    )
    regressor = HistGradientBoostingRegressor(
        # A conservative upper-middle estimate is appropriate for a routing
        # safety model: held-out false-low AADT-bin errors fall by half versus
        # the conditional median while overall error still beats the fallback.
        loss="quantile",
        quantile=float(model["quantile"]),
        max_iter=int(model["max_iter"]),
        learning_rate=float(model["learning_rate"]),
        max_leaf_nodes=int(model["max_leaf_nodes"]),
        min_samples_leaf=int(model["min_samples_leaf"]),
        l2_regularization=float(model["l2_regularization"]),
        categorical_features=list(range(len(_CATEGORICAL))),
        random_state=int(model["random_state"]),
    )
    return Pipeline([("features", preprocessing), ("model", regressor)])


def _weights(station_keys: pd.Series) -> np.ndarray:
    counts = station_keys.value_counts()
    return station_keys.map(1.0 / counts).to_numpy(dtype="float64")


def predict(
    streets: gpd.GeoDataFrame,
    station_keys: pd.Series,
    measured: pd.Series,
    params: Params,
) -> Prediction:
    """Fit the configured model and predict only within its supported domain."""
    empty = pd.Series(np.nan, index=streets.index, dtype="float64")
    unsupported = pd.Series(False, index=streets.index, dtype=bool)
    if not bool(params.get("aadt.model.enabled", False)):
        return Prediction(empty, unsupported, 0)

    target = pd.to_numeric(measured, errors="coerce")
    observed = target.notna()
    keys = station_keys.astype("string").fillna("").astype(str)
    station_count = int(keys[observed].nunique())
    if station_count < int(params["aadt.model.min_station_keys"]):
        return Prediction(empty, unsupported, station_count)

    features = feature_frame(streets, params)
    train = features.loc[observed]
    estimator = _estimator(params)
    estimator.fit(
        train,
        np.log1p(target.loc[observed]),
        model__sample_weight=_weights(keys.loc[observed]),
    )

    values = pd.Series(
        np.expm1(estimator.predict(features)),
        index=streets.index,
        dtype="float64",
    ).clip(
        lower=float(params["aadt.impute_floor"]),
        upper=float(params["aadt.model.max_aadt"]),
    )

    supported = _support_mask(streets, features, keys, observed, params)
    return Prediction(values, supported, station_count)


def _support_mask(
    streets: gpd.GeoDataFrame,
    features: pd.DataFrame,
    station_keys: pd.Series,
    observed: pd.Series,
    params: Params,
) -> pd.Series:
    """Reject road classes and categories not represented by enough stations."""
    model = params["aadt.model"]
    supported = pd.to_numeric(
        _source_column(streets, "rdclass"), errors="coerce"
    ).isin([int(value) for value in model["eligible_rdclasses"]])

    minimum = int(model["min_category_station_keys"])
    training = features.loc[observed].copy()
    training["__station_key"] = station_keys.loc[observed]
    for column in _CATEGORICAL:
        counts = training.groupby(column)["__station_key"].nunique()
        supported &= features[column].map(counts).fillna(0).ge(minimum)
    return supported.astype(bool)


def cross_validate(
    streets: gpd.GeoDataFrame,
    station_keys: pd.Series,
    measured: pd.Series,
    route_groups: pd.Series,
    params: Params,
) -> dict[str, dict[str, float]]:
    """Compare the model with the current median cascade on held-out routes.

    Grouping by route is stricter than a random split: adjacent count stations
    on one named/state route cannot appear in both training and validation.
    Results are aggregated once per station key so split centreline geometry
    does not inflate the sample size or the score.
    """
    target = pd.to_numeric(measured, errors="coerce")
    observed = target.notna()
    data = streets.loc[observed].copy()
    data["__station_key"] = (
        station_keys.loc[observed].astype("string").fillna("").astype(str)
    )
    data["__target"] = target.loc[observed].astype(float)
    data["__route_group"] = (
        route_groups.loc[observed]
        .astype("string")
        .fillna(data["__station_key"])
        .astype(str)
    )
    features = feature_frame(data, params)

    model_rows: list[pd.DataFrame] = []
    baseline_rows: list[pd.DataFrame] = []
    splitter = GroupKFold(int(params["aadt.model.cv_folds"]))
    for train_pos, test_pos in splitter.split(
        features, data["__target"], data["__route_group"]
    ):
        train = data.iloc[train_pos]
        test = data.iloc[test_pos]
        estimator = _estimator(params)
        estimator.fit(
            features.iloc[train_pos],
            np.log1p(train["__target"]),
            model__sample_weight=_weights(train["__station_key"]),
        )
        model_rows.append(
            pd.DataFrame(
                {
                    "station": test["__station_key"].to_numpy(),
                    "target": test["__target"].to_numpy(),
                    "prediction": np.expm1(
                        estimator.predict(features.iloc[test_pos])
                    ),
                    "eligible": test["rdclass"].isin(
                        params["aadt.model.eligible_rdclasses"]
                    ).to_numpy(),
                }
            )
        )
        baseline_rows.append(
            pd.DataFrame(
                {
                    "station": test["__station_key"].to_numpy(),
                    "target": test["__target"].to_numpy(),
                    "prediction": _fold_medians(train, test, params).to_numpy(),
                    "eligible": test["rdclass"].isin(
                        params["aadt.model.eligible_rdclasses"]
                    ).to_numpy(),
                }
            )
        )

    return {
        "median": _metrics(baseline_rows, params),
        "model": _metrics(model_rows, params),
    }


def _fold_medians(
    train: pd.DataFrame, test: pd.DataFrame, params: Params
) -> pd.Series:
    prediction = pd.Series(np.nan, index=test.index, dtype="float64")
    for keys in params["aadt.impute_groups"]:
        missing = prediction.isna()
        medians = train.groupby(list(keys), dropna=False)["__target"].median()
        lookup = test.loc[missing].set_index(list(keys)).index
        prediction.loc[missing] = pd.Series(
            lookup.map(medians), index=test.index[missing], dtype="float64"
        )
    return prediction.fillna(float(params["aadt.impute_floor"]))


def _metrics(
    folds: list[pd.DataFrame], params: Params
) -> dict[str, float]:
    rows = pd.concat(folds, ignore_index=True)
    rows = rows[rows["eligible"]]
    by_station = rows.groupby("station").agg(
        target=("target", "first"),
        prediction=("prediction", "median"),
    )
    actual = by_station["target"].to_numpy()
    predicted = by_station["prediction"].clip(
        lower=float(params["aadt.impute_floor"]),
        upper=float(params["aadt.model.max_aadt"]),
    ).to_numpy()
    breaks = [
        float(params["lts.mixed.aadt_break_low"]),
        float(params["lts.mixed.aadt_break_mid"]),
    ]
    return {
        "station_keys": int(len(by_station)),
        "rmsle": round(float(mean_squared_log_error(actual, predicted) ** 0.5), 4),
        "mae": round(float(mean_absolute_error(actual, predicted)), 1),
        "median_ape_pct": round(
            float(np.median(np.abs(predicted - actual) / actual) * 100), 1
        ),
        "lts_bin_accuracy_pct": round(
            float(
                np.mean(
                    np.digitize(actual, breaks) == np.digitize(predicted, breaks)
                )
                * 100
            ),
            1,
        ),
    }

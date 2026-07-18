from __future__ import annotations

import importlib.util
import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder


def _preprocessor(frame: pd.DataFrame) -> ColumnTransformer:
    numeric = list(frame.select_dtypes(include=["number", "bool"]).columns)
    categorical = [c for c in frame.columns if c not in numeric]
    return ColumnTransformer([
        ("num", SimpleImputer(strategy="median", add_indicator=True), numeric),
        ("cat", Pipeline([
            ("impute", SimpleImputer(strategy="most_frequent")),
            ("onehot", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
        ]), categorical),
    ], verbose_feature_names_out=False)


@dataclass
class ProbabilisticEstimator:
    kind: str
    random_state: int = 42
    strict_backend: bool = False
    pipeline: Any = None
    backend: str = ""
    residual_sigma: float = 1.0
    classes_: list[str] | None = None
    survival_times_: np.ndarray | None = None
    survival_probs_: np.ndarray | None = None

    def fit(self, X: pd.DataFrame, y: Any, event: Any = None):
        if self.kind == "survival":
            return self._fit_survival(X, np.asarray(y, float), np.asarray(event, bool))
        prep = _preprocessor(X)
        has_ngboost = importlib.util.find_spec("ngboost") is not None
        if self.strict_backend and not has_ngboost and self.kind in {"classification", "positive_regression"}:
            raise RuntimeError("ngboost is required when strict_backend=true")
        if self.kind in {"classification", "multiclass"}:
            if has_ngboost:
                from ngboost import NGBClassifier
                estimator = NGBClassifier(random_state=self.random_state, verbose=False)
                self.backend = "ngboost"
            else:
                estimator = HistGradientBoostingClassifier(random_state=self.random_state)
                self.backend = "sklearn_fallback"
            self.pipeline = Pipeline([("prep", prep), ("model", estimator)])
            self.pipeline.fit(X, y)
            self.classes_ = [str(v) for v in self.pipeline.named_steps["model"].classes_]
        else:
            raw_y = np.asarray(y, float)
            log_y = np.log(np.clip(raw_y, 1e-12, None))
            if has_ngboost:
                from ngboost import NGBRegressor
                from ngboost.distns import Normal
                estimator = NGBRegressor(Dist=Normal, random_state=self.random_state, verbose=False)
                self.backend = "ngboost"
            else:
                estimator = HistGradientBoostingRegressor(random_state=self.random_state)
                self.backend = "sklearn_fallback"
            self.pipeline = Pipeline([("prep", prep), ("model", estimator)])
            self.pipeline.fit(X, log_y)
            residual = log_y - self.pipeline.predict(X)
            self.residual_sigma = max(float(np.std(residual)), 1e-6)
        return self

    def _fit_survival(self, X: pd.DataFrame, duration: np.ndarray, event: np.ndarray):
        if importlib.util.find_spec("sksurv") is not None:
            from sksurv.ensemble import RandomSurvivalForest
            from sksurv.util import Surv
            self.pipeline = Pipeline([
                ("prep", _preprocessor(X)),
                ("model", RandomSurvivalForest(n_estimators=200, min_samples_leaf=3, random_state=self.random_state)),
            ])
            self.pipeline.fit(X, Surv.from_arrays(event, duration))
            self.backend = "scikit-survival"
        else:
            if self.strict_backend:
                raise RuntimeError("scikit-survival is required when strict_backend=true")
            # Product-limit estimate: a distributionally valid baseline until the full backend is installed.
            times, survival, at_risk = [], [], len(duration)
            current = 1.0
            for t in np.unique(duration):
                deaths = int(np.sum((duration == t) & event))
                censored = int(np.sum((duration == t) & ~event))
                if deaths and at_risk:
                    current *= 1.0 - deaths / at_risk
                times.append(float(t)); survival.append(current)
                at_risk -= deaths + censored
            self.survival_times_ = np.asarray(times)
            self.survival_probs_ = np.asarray(survival)
            self.backend = "kaplan_meier_fallback"
        return self

    def predict_distribution(self, X: pd.DataFrame, quantiles: list[float], horizons: list[int]) -> list[dict[str, Any]]:
        if self.kind == "survival":
            return self._predict_survival(X, horizons)
        if self.kind in {"classification", "multiclass"}:
            probs = self.pipeline.predict_proba(X)
            return [{"distribution": "categorical", "parameters": {c: float(p) for c, p in zip(self.classes_ or [], row)}} for row in probs]
        model = self.pipeline.named_steps["model"]
        transformed = self.pipeline.named_steps["prep"].transform(X)
        if self.backend == "ngboost":
            dist = model.pred_dist(transformed)
            mus = np.asarray(dist.params["loc"])
            sigmas = np.asarray(dist.params["scale"])
        else:
            mus = np.asarray(model.predict(transformed))
            sigmas = np.repeat(self.residual_sigma, len(X))
        from statistics import NormalDist
        output = []
        for mu, sigma in zip(mus, sigmas):
            qs = {str(q): math.exp(float(mu) + float(sigma) * NormalDist().inv_cdf(q)) for q in quantiles}
            output.append({"distribution": "lognormal", "parameters": {"log_mu": float(mu), "log_sigma": float(sigma)}, "quantiles": qs})
        return output

    def _predict_survival(self, X: pd.DataFrame, horizons: list[int]) -> list[dict[str, Any]]:
        if self.backend == "scikit-survival":
            functions = self.pipeline.predict_survival_function(X)
            curves = [{str(h): float(fn(h)) for h in horizons} for fn in functions]
        else:
            def prob(h):
                idx = np.searchsorted(self.survival_times_, h, side="right") - 1
                return 1.0 if idx < 0 else float(self.survival_probs_[idx])
            curves = [{str(h): prob(h) for h in horizons} for _ in range(len(X))]
        return [{"distribution": "survival_curve", "parameters": {"survival_probability_by_day": curve}} for curve in curves]

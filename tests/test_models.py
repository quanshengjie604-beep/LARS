import numpy as np
import pandas as pd

from decision_engine.models import ProbabilisticEstimator

X = pd.DataFrame({"numeric": [1.0, 2.0, None, 4.0], "category": ["a", "b", "a", None]})


def test_classifier_distribution_sums_to_one():
    model = ProbabilisticEstimator("classification").fit(X, np.array([False, True, False, True]))
    output = model.predict_distribution(X.iloc[:1], [0.1, 0.5, 0.9], [365])
    assert abs(sum(output[0]["parameters"].values()) - 1.0) < 1e-8


def test_positive_regression_is_lognormal():
    model = ProbabilisticEstimator("positive_regression").fit(X, np.array([1.0, 2.0, 1.5, 4.0]))
    output = model.predict_distribution(X.iloc[:1], [0.1, 0.5, 0.9], [365])
    assert output[0]["distribution"] == "lognormal"
    assert output[0]["quantiles"]["0.1"] > 0


def test_survival_fallback_is_monotone():
    model = ProbabilisticEstimator("survival").fit(X, np.array([10, 20, 30, 40]), np.array([1, 0, 1, 0]))
    output = model.predict_distribution(X.iloc[:1], [], [5, 15, 35])[0]
    values = list(output["parameters"]["survival_probability_by_day"].values())
    assert values == sorted(values, reverse=True)

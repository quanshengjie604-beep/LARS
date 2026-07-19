"""
Run_MC.py
=========

Entry point that connects the WebApp to the Monte Carlo analysis code living in
``Scripts/03_Monte_Carlo``.

``run_singular_profile_analysis`` reuses the demo's inputs and the Monte Carlo
engine to run a single profile, then returns a JSON-serialisable dict containing:

  * ``summary``   -- the numeric summary from ``aggregate.summarize`` (dict)
  * ``dashboard`` -- the existing matplotlib decision dashboard, rendered to a
                     PNG and embedded as a base64 ``data:`` URI so the browser
                     can show it with a plain ``<img>`` tag.

Later, this is where you'll accept parameters from the web form instead of the
hard-coded demo inputs.

Import note
-----------
The Monte Carlo package directory is named ``03_Monte_Carlo`` (it starts with a
digit), so its modules import each other by plain name and rely on that
directory being on ``sys.path``. When you run ``demo.py`` directly, Python adds
its directory automatically; here we add it explicitly before importing.
"""

from __future__ import annotations

import base64
import io
import sys
from pathlib import Path
from typing import Any

# Repo layout:  <repo>/WebApp/Run_MC.py  ->  <repo>/Scripts/03_Monte_Carlo
_REPO_ROOT = Path(__file__).resolve().parent.parent
_MC_DIR = _REPO_ROOT / "Scripts" / "03_Monte_Carlo"
_SRC_DIR = _REPO_ROOT / "src"

# Six-model NGBoost checkpoint used to score a live founder profile. This is the
# augmented-v2 demo checkpoint (all of m1..m6 present; m5 emits the 4-way exit
# categorical incl. "secondary"). Override with LARS_CHECKPOINT_CONFIG if needed.
_DEFAULT_CHECKPOINT_CONFIG = _REPO_ROOT / "artifacts" / "demo_checkpoint" / "demo_config.yaml"

# Monte Carlo simulation size for a single profile. Fewer paths than the 100k
# demo so a live web request returns quickly; still plenty for stable aggregates.
_MC_ITERATIONS = 50_000
_MC_SEED = 42


def _ensure_runtime() -> None:
    """Make the MC package + decision_engine importable and force a headless MPL.

    The Monte Carlo package directory starts with a digit (``03_Monte_Carlo``),
    so its modules import each other by plain name and need that directory on
    ``sys.path``; ``decision_engine`` lives under ``src``. Matplotlib is switched
    to the ``Agg`` backend before any pyplot import so rendering works inside the
    (display-less) web server process.
    """
    for directory in (str(_MC_DIR), str(_SRC_DIR)):
        if directory not in sys.path:
            sys.path.insert(0, directory)
    import matplotlib
    matplotlib.use("Agg")


def _fig_to_data_uri(fig) -> str:
    """Encode a matplotlib figure as a base64 PNG ``data:`` URI and close it."""
    import matplotlib.pyplot as plt

    buf = io.BytesIO()
    # Preserve the figure's own background (the dashboard uses a warm off-white).
    fig.savefig(buf, format="png", dpi=150, facecolor=fig.get_facecolor())
    plt.close(fig)  # release the figure so we don't leak memory across requests
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def run_singular_profile_analysis() -> dict:
    """Run the Monte Carlo analysis for a single opportunity profile.

    Uses the same hard-coded inputs as ``demo.py`` (``synthetic_theta`` and
    ``thesis_terms``), runs the simulation, and returns a dict with the numeric
    ``summary`` and a rendered ``dashboard`` image (base64 PNG data URI).
    """
    # Ensure the Monte Carlo package directory is importable by plain name.
    mc_dir = str(_MC_DIR)
    if mc_dir not in sys.path:
        sys.path.insert(0, mc_dir)

    # Force a headless backend before importing pyplot so this works inside the
    # web server process (no display, no GUI event loop).
    import matplotlib
    matplotlib.use("Agg")

    # Imports must follow the sys.path adjustment above.
    from demo import synthetic_theta, thesis_terms  # noqa: E402
    from contracts import SimControl  # noqa: E402
    from simulator import simulate_paths  # noqa: E402
    from aggregate import summarize  # noqa: E402
    from Utilities.plot_paths import plot_dashboard, _stage_names_from  # noqa: E402

    params = synthetic_theta()
    terms = thesis_terms()
    control = SimControl(n_iterations=100_000, random_seed=42)

    paths = simulate_paths(params, terms, control)
    result = summarize(paths, params, terms)

    # Render the existing decision dashboard to an in-memory PNG (no file I/O).
    fig = plot_dashboard(
        paths,
        stage_names=_stage_names_from(params),
        benchmark=30.0,
        opportunity_id=params.opportunity_id,
        n_iterations=control.n_iterations,
    )
    dashboard_uri = _fig_to_data_uri(fig)

    return {
        # ``summarize`` returns a dataclass-like result; expose it as a plain dict.
        "summary": dict(result.__dict__),
        "dashboard": dashboard_uri,
    }


def run_profile_analysis(
    inference_request: dict[str, Any],
    *,
    n_iterations: int = _MC_ITERATIONS,
    random_seed: int = _MC_SEED,
) -> dict:
    """Score one live founder profile end-to-end and render its dashboard.

    Pipeline (a single profile flows straight through, no files):

        inference_request  --NGBoost six-model checkpoint-->  prediction record
        prediction record  --theta_from_prediction-->         DistributionParams
        DistributionParams --Monte Carlo simulate/aggregate--> SimulationResult
        SimulationResult / paths --plot_dashboard-->          PNG data URI

    ``inference_request`` is a schema-clean request as produced by
    ``WebApp.enrich.enrich_profile_from_name`` (or the questionnaire). Because
    the M6 exit-value model emits absolute USD, the simulator is run in
    ``exit_value_mode="absolute"``.

    Returns a JSON-serialisable dict with the numeric ``summary``, the rendered
    ``dashboard`` (base64 PNG data URI) and the six-model ``prediction`` block
    (handy for debugging / provenance; the frontend renders only the dashboard).
    """
    _ensure_runtime()

    # decision_engine (NGBoost checkpoint loading + inference).
    from decision_engine.io import load_config  # noqa: E402
    from decision_engine.pipeline import infer_records  # noqa: E402

    # Monte Carlo layer.
    from theta_from_prediction import theta_from_prediction, default_terms  # noqa: E402
    from contracts import SimControl  # noqa: E402
    from simulator import simulate_paths  # noqa: E402
    from aggregate import summarize  # noqa: E402
    from Utilities.plot_paths import plot_dashboard, _stage_names_from  # noqa: E402

    import os

    config_path = os.environ.get("LARS_CHECKPOINT_CONFIG", str(_DEFAULT_CHECKPOINT_CONFIG))
    config = load_config(config_path)
    # model_dir in the config is relative to the repo root; resolve it so this
    # works regardless of the server's current working directory.
    model_dir = Path(config["project"]["model_dir"])
    if not model_dir.is_absolute():
        config["project"]["model_dir"] = str(_REPO_ROOT / model_dir)

    # 1) NGBoost inference on the single profile -> one prediction record.
    predictions = infer_records(config, [inference_request])
    if not predictions:
        raise ValueError("NGBoost inference produced no prediction for this profile.")
    prediction = predictions[0]

    # 2) Prediction -> theta (DistributionParams) for the simulator.
    params = theta_from_prediction(prediction)
    terms = default_terms()

    # 3) Single Monte Carlo simulation. M6 is absolute USD -> "absolute" mode.
    control = SimControl(
        n_iterations=n_iterations,
        random_seed=random_seed,
        exit_value_mode="absolute",
    )
    paths = simulate_paths(params, terms, control)
    result = summarize(paths, params, terms)

    # 4) Render the decision dashboard to an in-memory PNG.
    fig = plot_dashboard(
        paths,
        stage_names=_stage_names_from(params),
        benchmark=30.0,
        opportunity_id=prediction.get("company_name") or params.opportunity_id,
        n_iterations=control.n_iterations,
    )
    dashboard_uri = _fig_to_data_uri(fig)

    return {
        "summary": dict(result.__dict__),
        "dashboard": dashboard_uri,
        "prediction": prediction,
    }


if __name__ == "__main__":
    import json

    out = run_singular_profile_analysis()
    # Print the summary; keep the (very long) base64 image out of the console.
    print(json.dumps(out["summary"], indent=2))
    print(f"\ndashboard: {len(out['dashboard']):,} chars of base64 PNG")

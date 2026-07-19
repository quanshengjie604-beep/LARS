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

# Repo layout:  <repo>/WebApp/Run_MC.py  ->  <repo>/Scripts/03_Monte_Carlo
_REPO_ROOT = Path(__file__).resolve().parent.parent
_MC_DIR = _REPO_ROOT / "Scripts" / "03_Monte_Carlo"


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


if __name__ == "__main__":
    import json

    out = run_singular_profile_analysis()
    # Print the summary; keep the (very long) base64 image out of the console.
    print(json.dumps(out["summary"], indent=2))
    print(f"\ndashboard: {len(out['dashboard']):,} chars of base64 PNG")

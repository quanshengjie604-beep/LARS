"""Standalone: per-company MC plots, one set per prediction record.

Same four figures as demo.py, but driven by the predictions JSONL (via the H2
adapter) and rendered once per company, with filenames adapted to the company:

    Output/company_plots/<company-slug>/
        mc_paths.png              life paths, segmented by funding stage
        mc_moic.png               MOIC-by-path bars vs. VC benchmark
        mc_dashboard.png          survival / benchmark / per-round hazards
        mc_dashboard_segments.png Loser / Contender / Winner breakdown

Usage (run from the repo root -- the package dir starts with a digit, so the
script's own directory is put on sys.path automatically):

    # every company (1,020 x 4 = 4,080 PNGs -- slow; see note below)
    python Scripts/03_Monte_Carlo/run_plots.py

    # just the first few, or filter by name
    python Scripts/03_Monte_Carlo/run_plots.py --limit 5
    python Scripts/03_Monte_Carlo/run_plots.py --company "10x"
    python Scripts/03_Monte_Carlo/run_plots.py --index 0

NOTE: in this demo checkpoint every record carries near-identical placeholder
parameters, so every company's plots look the same. Rendering all 4,080 is
therefore mostly wasted here -- use --limit / --company while iterating and only
run the full sweep once the predictions carry real per-startup signal.
"""

from __future__ import annotations

import argparse
import os
import re
import sys

import matplotlib

matplotlib.use("Agg")  # headless: only write PNGs
import matplotlib.pyplot as plt  # noqa: E402

from contracts import SimControl  # noqa: E402
from simulator import simulate_paths  # noqa: E402
from theta_from_prediction import (  # noqa: E402
    default_terms,
    load_predictions,
    theta_from_prediction,
)
from Utilities.plot_paths import (  # noqa: E402
    plot_paths,
    plot_moic_bars,
    plot_dashboard,
    plot_segment_dashboard,
    _stage_names_from,
)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DEFAULT_INPUT = os.path.join(_REPO_ROOT, "artifacts", "scrape_demo_predictions.jsonl")
_DEFAULT_OUTDIR = os.path.join(_REPO_ROOT, "Output", "company_plots")
_BENCHMARK = 30.0


def _slugify(name: str) -> str:
    """Filesystem-safe folder name from a company name."""
    slug = re.sub(r"[^A-Za-z0-9]+", "_", (name or "").strip()).strip("_").lower()
    return slug or "unknown"


def _plot_one(record: dict, outroot: str, n_iterations: int, seed: int, slug: str) -> str:
    """Render the four figures for a single company. Returns the output folder."""
    params = theta_from_prediction(record)
    terms = default_terms()
    control = SimControl(
        n_iterations=n_iterations,
        random_seed=seed,
        exit_value_mode="absolute",  # M6 is USD, matching run_pipeline
    )

    paths = simulate_paths(params, terms, control)
    stage_names = _stage_names_from(params)
    company = record.get("company_name") or params.opportunity_id

    outdir = os.path.join(outroot, slug)
    os.makedirs(outdir, exist_ok=True)

    # 1. life paths (sample ~5% of paths, as in demo.py)
    n_show = max(1, int(n_iterations * 0.05))
    fig, _ = plot_paths(
        paths,
        horizon=terms.horizon_years,
        stage_names=stage_names,
        n_show=n_show,
        sort_by="moic",
        title=f"Monte Carlo company life paths — {company}",
        save_path=os.path.join(outdir, "mc_paths.png"),
    )
    plt.close(fig)

    # 2. MOIC by path
    fig, _ = plot_moic_bars(
        paths,
        n_show=n_iterations,
        sort_by="moic",
        benchmark=_BENCHMARK,
        title=f"Monte Carlo MOIC by path — {company}",
        save_path=os.path.join(outdir, "mc_moic.png"),
    )
    plt.close(fig)

    # 3. decision dashboard
    fig = plot_dashboard(
        paths,
        stage_names=stage_names,
        benchmark=_BENCHMARK,
        opportunity_id=company,
        n_iterations=n_iterations,
        save_path=os.path.join(outdir, "mc_dashboard.png"),
    )
    plt.close(fig)

    # 4. outcome-segmented dashboard
    fig = plot_segment_dashboard(
        paths,
        stage_names=stage_names,
        benchmark=_BENCHMARK,
        opportunity_id=company,
        n_iterations=n_iterations,
        save_path=os.path.join(outdir, "mc_dashboard_segments.png"),
    )
    plt.close(fig)

    return outdir


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", default=_DEFAULT_INPUT, help="predictions JSONL path")
    ap.add_argument("--outdir", default=_DEFAULT_OUTDIR, help="root folder for plots")
    ap.add_argument("--iterations", type=int, default=10_000, help="MC paths per company")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--limit", type=int, default=None, help="cap number of companies")
    ap.add_argument("--index", type=int, default=None, help="render only this row index")
    ap.add_argument("--company", default=None,
                    help="case-insensitive substring filter on company name")
    args = ap.parse_args()

    seen: dict[str, int] = {}
    done = 0
    for i, record in enumerate(load_predictions(args.input)):
        if args.index is not None and i != args.index:
            continue
        name = record.get("company_name") or record.get("opportunity_id", "")
        if args.company and args.company.lower() not in name.lower():
            continue

        # Unique folder even when two companies share a name.
        slug = _slugify(name)
        if slug in seen:
            seen[slug] += 1
            slug = f"{slug}_{seen[slug]}"
        else:
            seen[slug] = 0

        outdir = _plot_one(record, args.outdir, args.iterations, args.seed, slug)
        done += 1
        print(f"[{done}] {name} -> {outdir}", file=sys.stderr)

        if args.index is not None:
            break
        if args.limit is not None and done >= args.limit:
            break

    print(f"Rendered plots for {done} companies under {args.outdir}")


if __name__ == "__main__":
    main()

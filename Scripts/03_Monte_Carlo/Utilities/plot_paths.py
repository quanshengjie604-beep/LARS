"""Validation plot: the life path of each simulated company on one timeline.

For a sample of Monte Carlo paths, draws one horizontal line per path from
t = 0 to the moment it terminates. Each line is **segmented and colored by the
funding stage** the company was in over time (seed / A / B / C+ ...), so you can
see exactly where on its journey it died or exited. Terminal markers:

    * filled dot   -> realized exit  (EXITED), colored by MOIC
    x  cross       -> death          (FAILED, ran out of cash)
    |  tick        -> stall          (STALLED, could not raise the next round)
    o  open circle -> censored       (alive at horizon / stayed private)

Paths are sorted by MOIC by default (exits float to the top).

Run directly (saves a PNG under <repo>/Output/):

    python Scripts/03_Monte_Carlo/Utilities/plot_paths.py

or import `plot_paths` and pass it the dict returned by `simulate_paths`.
"""

from __future__ import annotations

import os
import sys

import numpy as np

# The MC package dir starts with a digit, so add it to sys.path and import the
# sibling modules (contracts / simulator / demo) by plain name.
_MC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _MC_DIR not in sys.path:
    sys.path.insert(0, _MC_DIR)

from contracts import PathStatus  # noqa: E402


def plot_paths(
    paths: dict[str, np.ndarray],
    horizon: float,
    stage_names: list[str] | None = None,
    n_show: int = 200,
    sort_by: str = "moic",
    sample_seed: int = 0,
    title: str | None = None,
    save_path: str | None = None,
    show: bool = False,
):
    """Render the per-path life-timeline validation plot, segmented by stage.

    paths       : dict from simulate_paths (needs moic, t_end, status,
                  stage_reached, stage_entry_t).
    horizon     : simulation horizon in years (x-axis limit).
    stage_names : labels for stage 0..K; defaults to "stage 0", "stage 1", ...
    n_show      : max number of paths to draw (random sample if fewer than N).
    sort_by     : 'moic' | 't_end' | 'stage' | 'none' -- vertical ordering.
    save_path   : if given, write the figure there (PNG).
    show        : if True, open an interactive window.
    """
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection
    from matplotlib.colors import LogNorm
    from matplotlib.cm import ScalarMappable
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    moic = np.asarray(paths["moic"], dtype=float)
    status = np.asarray(paths["status"])
    t_end = np.asarray(paths["t_end"], dtype=float)
    t_end = np.where(np.isfinite(t_end), t_end, horizon)  # safety net
    stage_reached = np.asarray(paths["stage_reached"], dtype=int)
    stage_entry_t = np.asarray(paths["stage_entry_t"], dtype=float)
    n = moic.size

    n_stages = stage_entry_t.shape[1]
    if stage_names is None:
        stage_names = [f"stage {k}" for k in range(n_stages)]

    # --- pick a sample of paths ------------------------------------------------
    idx = np.arange(n)
    if n > n_show:
        idx = np.random.default_rng(sample_seed).choice(n, size=n_show, replace=False)

    if sort_by == "moic":
        idx = idx[np.argsort(moic[idx], kind="stable")]
    elif sort_by == "t_end":
        idx = idx[np.argsort(t_end[idx], kind="stable")]
    elif sort_by == "stage":
        idx = idx[np.argsort(stage_reached[idx], kind="stable")]

    # --- stage colours (categorical) and MOIC colours (exit dots) --------------
    # tab10 indices, skipping 3 (red) so stage colours never clash with the red
    # death cross.
    stage_cmap = plt.get_cmap("tab10")
    _stage_idx = [0, 1, 2, 4, 5, 6, 7, 8, 9]
    stage_colors = [stage_cmap(_stage_idx[k % len(_stage_idx)]) for k in range(n_stages)]

    moic_cmap = plt.get_cmap("viridis")
    pos = moic[idx][moic[idx] > 0]
    if pos.size:
        vmin = max(float(np.percentile(pos, 1)), 1e-2)
        vmax = max(float(np.percentile(pos, 99)), vmin * 10)
        moic_norm = LogNorm(vmin=vmin, vmax=vmax, clip=True)
    else:
        moic_norm = None

    def moic_color(val: float):
        if val <= 0 or moic_norm is None:
            return (0.6, 0.6, 0.6, 1.0)
        return moic_cmap(moic_norm(val))

    # --- build one line segment per (path, stage) ------------------------------
    segments: list = []
    seg_colors: list = []
    for row, i in enumerate(idx):
        sr = int(stage_reached[i])
        for j in range(sr + 1):
            t0 = stage_entry_t[i, j]
            t0 = float(t0) if np.isfinite(t0) else 0.0
            if j < sr:
                t1 = stage_entry_t[i, j + 1]
                t1 = float(t1) if np.isfinite(t1) else t_end[i]
            else:
                t1 = float(t_end[i])
            segments.append([(row, t0), (row, t1)])
            seg_colors.append(stage_colors[j])

    y = np.arange(idx.size)
    m = moic[idx]
    te = t_end[idx]
    st = status[idx]

    # --- figure ----------------------------------------------------------------
    width = max(6.0, min(0.06 * idx.size, 18.0))
    fig, ax = plt.subplots(figsize=(width, 8))

    ax.add_collection(
        LineCollection(segments, colors=seg_colors, linewidths=1.4, alpha=0.95)
    )

    # --- terminal markers (path # on x, time on y) -----------------------------
    ex = st == PathStatus.EXITED
    if ex.any():
        ax.scatter(y[ex], te[ex], c=[moic_color(v) for v in m[ex]],
                   edgecolors="black", linewidths=0.5, s=36, zorder=6)
    de = st == PathStatus.FAILED
    if de.any():
        ax.scatter(y[de], te[de], marker="x", c="#c0392b", s=34, linewidths=1.3, zorder=5)
    stl = st == PathStatus.STALLED
    if stl.any():
        ax.scatter(y[stl], te[stl], marker="_", c="#2c3e50", s=46, linewidths=1.3, zorder=5)
    cen = st == PathStatus.CENSORED
    if cen.any():
        ax.scatter(y[cen], te[cen], marker="o", facecolors="none",
                   edgecolors="#2c3e50", s=26, linewidths=0.9, zorder=5)

    ax.axhline(horizon, ls="--", lw=1.0, color="#95a5a6", alpha=0.8)
    ax.text(idx.size, horizon, "horizon ", va="bottom", ha="right",
            fontsize=8, color="#7f8c8d")

    ax.set_xlim(-1, idx.size)
    ax.set_ylim(0, horizon * 1.02)
    ax.set_xlabel(f"Path #  (sample of {idx.size:,} of {n:,}, sorted by {sort_by})")
    ax.set_ylabel("Time (years)")
    ax.set_title(title or "Monte Carlo company life paths (by funding stage)")

    # --- colorbar (MOIC of exits) ----------------------------------------------
    if moic_norm is not None:
        sm = ScalarMappable(norm=moic_norm, cmap=moic_cmap)
        sm.set_array([])
        cbar = fig.colorbar(sm, ax=ax, pad=0.02, fraction=0.035)
        cbar.set_label("Exit MOIC (log scale)")

    # --- two legends: stage colours (lines) and terminal markers ---------------
    stage_handles = [
        Patch(color=stage_colors[k], label=stage_names[k]) for k in range(n_stages)
    ]
    leg_stage = ax.legend(handles=stage_handles, title="Funding stage",
                          loc="upper right", framealpha=0.9, fontsize=8)
    ax.add_artist(leg_stage)

    marker_handles = [
        Line2D([0], [0], marker="o", color="w", markerfacecolor="#3fa34d",
               markeredgecolor="black", markersize=8, label="Exit (colored by MOIC)"),
        Line2D([0], [0], marker="x", color="#c0392b", markersize=8,
               linestyle="None", label="Death"),
        Line2D([0], [0], marker="_", color="#2c3e50", markersize=10,
               linestyle="None", label="Stalled"),
        Line2D([0], [0], marker="o", color="w", markerfacecolor="none",
               markeredgecolor="#2c3e50", markersize=8, label="Censored"),
    ]
    ax.legend(handles=marker_handles, loc="upper left", framealpha=0.9, fontsize=8)

    fig.tight_layout()
    if save_path:
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        fig.savefig(save_path, dpi=150)
        print(f"saved: {save_path}")
    if show:
        plt.show()
    return fig, ax


def _stage_names_from(params) -> list[str]:
    rp = params.round_progression
    if not rp:
        return ["stage 0"]
    return [rp[0].from_stage] + [tr.to_stage for tr in rp]


def _demo() -> None:
    import matplotlib
    matplotlib.use("Agg")  # headless: just write the PNG

    from demo import synthetic_theta, thesis_terms
    from contracts import SimControl
    from simulator import simulate_paths

    params = synthetic_theta()
    terms = thesis_terms()
    control = SimControl(n_iterations=5_000, random_seed=42)

    paths = simulate_paths(params, terms, control)

    repo_root = os.path.dirname(os.path.dirname(_MC_DIR))  # .../LARS
    out = os.path.join(repo_root, "Output", "mc_paths.png")
    plot_paths(
        paths,
        horizon=terms.horizon_years,
        stage_names=_stage_names_from(params),
        n_show=200,
        sort_by="moic",
        title=f"Monte Carlo company life paths ({params.opportunity_id})",
        save_path=out,
    )


if __name__ == "__main__":
    _demo()

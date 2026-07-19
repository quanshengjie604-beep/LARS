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


def plot_moic_bars(
    paths: dict[str, np.ndarray],
    n_show: int = 1000,
    sort_by: str = "moic",
    benchmark: float = 30.0,
    sample_seed: int = 0,
    title: str | None = None,
    save_path: str | None = None,
    show: bool = False,
):
    """Bar chart of per-path MOIC with a horizontal VC-benchmark reference line.

    paths     : dict from simulate_paths (needs moic).
    n_show    : max number of bars to draw (random sample if fewer than N).
    sort_by   : 'moic' (ascending, monotonic) | 'none'.
    benchmark : MOIC level for the "VC Benchmark Return" reference line.

    Total-loss paths (MOIC == 0) are excluded so the chart shows only the paths
    that returned something; the legend still reports shares of the full run.
    """
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    moic = np.asarray(paths["moic"], dtype=float)
    n = moic.size

    # Keep only paths that returned capital (drop the MOIC == 0 total losses).
    idx = np.flatnonzero(moic > 0)
    n_returning = idx.size
    if n_returning > n_show:
        idx = np.random.default_rng(sample_seed).choice(idx, size=n_show, replace=False)
    if sort_by == "moic":
        idx = idx[np.argsort(moic[idx], kind="stable")]

    m = moic[idx]
    x = np.arange(idx.size)
    beats = m >= benchmark
    bar_colors = np.where(beats, "#f1c40f", "#4c78a8")

    width = max(6.0, min(0.012 * idx.size, 20.0))
    fig, ax = plt.subplots(figsize=(width, 6))
    ax.bar(x, m, width=1.0, color=bar_colors, linewidth=0)

    ax.axhline(benchmark, ls="--", lw=1.6, color="#c0392b", zorder=5)
    ax.text(0.0, benchmark, f"  VC Benchmark Return ({benchmark:g}x)",
            va="bottom", ha="left", color="#c0392b", fontsize=9, zorder=6)

    y_top = (m.max() if m.size else benchmark)
    ax.set_xlim(-0.5, max(idx.size - 0.5, 0.5))
    ax.set_ylim(0, max(y_top, benchmark) * 1.08)
    ax.set_xlabel(
        f"Path #  (sample of {idx.size:,} of {n_returning:,} returning paths; "
        f"{n:,} total, sorted by {sort_by})"
    )
    ax.set_ylabel("MOIC")
    ax.set_title(title or "Monte Carlo MOIC by path (excl. total losses)")

    share = float(np.mean(moic >= benchmark))
    handles = [
        Patch(color="#f1c40f", label=f"MOIC ≥ benchmark ({share:.1%} of all paths)"),
        Patch(color="#4c78a8", label="MOIC < benchmark"),
        Line2D([0], [0], ls="--", color="#c0392b", label="VC Benchmark Return"),
    ]
    ax.legend(handles=handles, loc="upper left", framealpha=0.9, fontsize=8)

    fig.tight_layout()
    if save_path:
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        fig.savefig(save_path, dpi=150)
        print(f"saved: {save_path}")
    if show:
        plt.show()
    return fig, ax


# Validated reference palette (light mode) from the dataviz design system.
_INK, _INK2, _MUTED = "#0b0b0b", "#52514e", "#898781"
_GRID, _AXIS = "#e1e0d9", "#c3c2b7"
_SURFACE, _PAGE = "#fcfcfb", "#f9f9f7"
_GOOD, _CRIT, _BLUE = "#0ca30c", "#d03b3b", "#2a78d6"


def plot_dashboard(
    paths: dict[str, np.ndarray],
    stage_names: list[str],
    benchmark: float = 30.0,
    opportunity_id: str = "",
    n_iterations: int | None = None,
    title: str | None = None,
    save_path: str | None = None,
    show: bool = False,
):
    """Decision dashboard: survival, benchmark odds, and per-round death/exit.

    Four panels:
      * P(survival)              -- share of paths with MOIC > 0
      * P(beat VC benchmark)     -- share of paths with MOIC >= benchmark
      * P(death | reached round) -- per-round death hazard
      * P(exit  | reached round) -- per-round exit hazard

    The two per-round panels are *conditional on the company reaching that round*,
    so they read as a funnel hazard rather than being dominated by early attrition.
    """
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter
    from matplotlib.patches import Rectangle

    moic = np.asarray(paths["moic"], dtype=float)
    status = np.asarray(paths["status"])
    stage_reached = np.asarray(paths["stage_reached"], dtype=int)
    n = moic.size
    k = len(stage_names)

    p_survival = float(np.mean(moic > 0))
    p_benchmark = float(np.mean(moic >= benchmark))

    reached = np.array([np.sum(stage_reached >= j) for j in range(k)], dtype=float)
    died = np.array(
        [np.sum((status == PathStatus.FAILED) & (stage_reached == j)) for j in range(k)],
        dtype=float,
    )
    exited = np.array(
        [np.sum((status == PathStatus.EXITED) & (stage_reached == j)) for j in range(k)],
        dtype=float,
    )
    with np.errstate(divide="ignore", invalid="ignore"):
        p_death = np.where(reached > 0, died / reached, 0.0)
        p_exit = np.where(reached > 0, exited / reached, 0.0)

    fig = plt.figure(figsize=(13, 8.5), facecolor=_PAGE)
    gs = fig.add_gridspec(
        2, 2, height_ratios=[0.8, 1.3], hspace=0.42, wspace=0.16,
        left=0.065, right=0.96, top=0.83, bottom=0.09,
    )

    # --- KPI tiles -------------------------------------------------------------
    def _kpi(ax, value: float, label: str, color: str, sub: str) -> None:
        ax.set_axis_off()
        ax.add_patch(Rectangle((0.0, 0.0), 1.0, 1.0, transform=ax.transAxes,
                     facecolor=_SURFACE, edgecolor=_GRID, linewidth=1.3, zorder=0))
        # colored accent stripe on the left edge
        ax.add_patch(Rectangle((0.0, 0.0), 0.012, 1.0, transform=ax.transAxes,
                     facecolor=color, edgecolor="none", zorder=1))
        ax.text(0.06, 0.83, label, transform=ax.transAxes, fontsize=12.5,
                color=_INK2, va="top", ha="left")
        ax.text(0.055, 0.52, f"{value:.1%}", transform=ax.transAxes, fontsize=46,
                color=_INK, va="center", ha="left", fontweight="bold")
        ax.add_patch(Rectangle((0.06, 0.20), 0.88, 0.075, transform=ax.transAxes,
                     facecolor=_GRID, edgecolor="none", zorder=1))
        ax.add_patch(Rectangle((0.06, 0.20), 0.88 * min(max(value, 0.0), 1.0), 0.075,
                     transform=ax.transAxes, facecolor=color, edgecolor="none", zorder=2))
        ax.text(0.06, 0.10, sub, transform=ax.transAxes, fontsize=9.5,
                color=_MUTED, va="center", ha="left")

    ax1 = fig.add_subplot(gs[0, 0])
    _kpi(ax1, p_survival, "Probability of survival   (MOIC > 0)", _GOOD,
         f"{p_survival * n:,.0f} of {n:,} paths returned capital")

    ax2 = fig.add_subplot(gs[0, 1])
    _kpi(ax2, p_benchmark,
         f"Probability of beating the VC benchmark   (MOIC ≥ {benchmark:g}x)", _BLUE,
         f"{p_benchmark * n:,.0f} of {n:,} paths cleared {benchmark:g}x")

    # --- per-round bar charts --------------------------------------------------
    def _bars(ax, vals: np.ndarray, color: str, title_txt: str, ylab: str) -> None:
        xs = np.arange(k)
        rects = ax.bar(xs, vals, width=0.62, color=color, zorder=3)
        ax.set_title(title_txt, fontsize=13, color=_INK, loc="left", pad=10,
                     fontweight="bold")
        ax.set_xticks(xs)
        ax.set_xticklabels(stage_names)
        ax.set_ylim(0, max(float(vals.max()) * 1.30, 0.02))
        ax.yaxis.set_major_formatter(PercentFormatter(xmax=1.0))
        ax.set_ylabel(ylab, fontsize=10, color=_INK2)
        ax.set_facecolor(_SURFACE)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_color(_AXIS)
        ax.spines["bottom"].set_color(_AXIS)
        ax.tick_params(colors=_MUTED, labelsize=10)
        ax.grid(axis="y", color=_GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        for rect, v in zip(rects, vals):
            ax.text(rect.get_x() + rect.get_width() / 2, v, f"{v:.1%}",
                    ha="center", va="bottom", fontsize=10, color=_INK2)

    ax3 = fig.add_subplot(gs[1, 0])
    _bars(ax3, p_death, _CRIT, "Probability of dying in each round",
          "P(death | reached round)")
    ax4 = fig.add_subplot(gs[1, 1])
    _bars(ax4, p_exit, _GOOD, "Probability of exiting in each round",
          "P(exit | reached round)")

    head = title or "VC Brain — Monte Carlo Decision Dashboard"
    fig.text(0.065, 0.93, head, ha="left", fontsize=18, fontweight="bold", color=_INK)
    bits = [b for b in (opportunity_id,
                        f"{n_iterations:,} simulated paths" if n_iterations else "",
                        "competing-risks stage-jump model") if b]
    fig.text(0.065, 0.885, "     ·     ".join(bits), ha="left",
             fontsize=10.5, color=_MUTED)

    if save_path:
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        fig.savefig(save_path, dpi=150, facecolor=_PAGE)
        print(f"saved: {save_path}")
    if show:
        plt.show()
    return fig


def plot_segment_dashboard(
    paths: dict[str, np.ndarray],
    stage_names: list[str],
    benchmark: float = 30.0,
    opportunity_id: str = "",
    n_iterations: int | None = None,
    title: str | None = None,
    save_path: str | None = None,
    show: bool = False,
):
    """Outcome-segmented dashboard: one column per Loser / Contender / Winner.

    Segments (by realized MOIC):
        Loser      MOIC == 0                 -- total loss
        Contender  0 < MOIC < benchmark      -- returned capital, below benchmark
        Winner     MOIC >= benchmark         -- cleared the VC benchmark

    Per segment (column): its share of all cases + contribution to total return,
    then the per-round death and exit profiles *within that segment* (share of the
    segment terminating in each round). Because survival / benchmark odds are
    definitional inside a segment, the headline is recast to share + return
    contribution.
    """
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter
    from matplotlib.patches import Rectangle

    moic = np.asarray(paths["moic"], dtype=float)
    status = np.asarray(paths["status"])
    stage_reached = np.asarray(paths["stage_reached"], dtype=int)
    n = moic.size
    k = len(stage_names)
    total_return = float(moic.sum())

    segments = [
        ("Losers", f"MOIC = 0", moic == 0.0, _CRIT),
        ("Contenders", f"0 < MOIC < {benchmark:g}x", (moic > 0.0) & (moic < benchmark), _BLUE),
        ("Winners", f"MOIC ≥ {benchmark:g}x", moic >= benchmark, _GOOD),
    ]

    def _by_round(mask, path_status):
        out = np.array(
            [np.sum(mask & (status == path_status) & (stage_reached == j)) for j in range(k)],
            dtype=float,
        )
        size = max(int(mask.sum()), 1)
        return out / size

    death = [_by_round(seg[2], PathStatus.FAILED) for seg in segments]
    exit_ = [_by_round(seg[2], PathStatus.EXITED) for seg in segments]
    death_max = max(0.02, max(d.max() for d in death))
    exit_max = max(0.02, max(e.max() for e in exit_))

    fig = plt.figure(figsize=(15, 9.6), facecolor=_PAGE)
    gs = fig.add_gridspec(
        3, 3, height_ratios=[1.05, 1.0, 1.0], hspace=0.42, wspace=0.2,
        left=0.055, right=0.975, top=0.865, bottom=0.07,
    )

    def _seg_kpi(ax, name, definition, share, color, sub_lines):
        ax.set_axis_off()
        ax.add_patch(Rectangle((0, 0), 1, 1, transform=ax.transAxes,
                     facecolor=_SURFACE, edgecolor=_GRID, linewidth=1.3, zorder=0))
        ax.add_patch(Rectangle((0, 0.955), 1, 0.045, transform=ax.transAxes,
                     facecolor=color, edgecolor="none", zorder=1))
        ax.text(0.055, 0.885, name, transform=ax.transAxes, fontsize=15.5,
                fontweight="bold", color=_INK, va="top")
        ax.text(0.055, 0.745, definition, transform=ax.transAxes, fontsize=10,
                color=_MUTED, va="top")
        ax.text(0.05, 0.53, f"{share:.1%}", transform=ax.transAxes, fontsize=32,
                fontweight="bold", color=_INK, va="center")
        y = 0.30
        for s in sub_lines:
            ax.text(0.055, y, s, transform=ax.transAxes, fontsize=10,
                    color=_INK2, va="center")
            y -= 0.095

    def _seg_bars(ax, vals, color, ylab, ymax, empty_note, show_x):
        xs = np.arange(k)
        rects = ax.bar(xs, vals, width=0.62, color=color, zorder=3)
        ax.set_ylim(0, ymax * 1.28)
        ax.set_xlim(-0.6, k - 0.4)
        ax.set_xticks(xs)
        ax.set_xticklabels(stage_names if show_x else [""] * k)
        ax.yaxis.set_major_formatter(PercentFormatter(xmax=1.0))
        if ylab:
            ax.set_ylabel(ylab, fontsize=10.5, color=_INK2)
        ax.set_facecolor(_SURFACE)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_color(_AXIS)
        ax.spines["bottom"].set_color(_AXIS)
        ax.tick_params(colors=_MUTED, labelsize=9.5)
        ax.grid(axis="y", color=_GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        if vals.sum() <= 0:
            ax.text(0.5, 0.5, empty_note, transform=ax.transAxes, ha="center",
                    va="center", fontsize=10, color=_MUTED, style="italic")
            return
        for rect, v in zip(rects, vals):
            if v > 0:
                ax.text(rect.get_x() + rect.get_width() / 2, v, f"{v:.1%}",
                        ha="center", va="bottom", fontsize=9.5, color=_INK2)

    for c, (name, definition, mask, color) in enumerate(segments):
        size = int(mask.sum())
        share = size / n
        seg_return = float(moic[mask].sum())
        ret_share = seg_return / total_return if total_return > 0 else 0.0
        mean_moic = float(moic[mask].mean()) if size else 0.0

        _seg_kpi(
            fig.add_subplot(gs[0, c]), name, definition, share, color,
            [f"{size:,} of {n:,} cases",
             f"avg MOIC {mean_moic:,.1f}x",
             f"{ret_share:.1%} of total return"],
        )
        _seg_bars(fig.add_subplot(gs[1, c]), death[c], _CRIT,
                  "Death by round\n(% of segment)" if c == 0 else "",
                  death_max, "no deaths in this segment", show_x=False)
        _seg_bars(fig.add_subplot(gs[2, c]), exit_[c], _GOOD,
                  "Exit by round\n(% of segment)" if c == 0 else "",
                  exit_max, "no exits in this segment", show_x=True)

    head = title or "VC Brain — Outcome-Segmented Dashboard"
    fig.text(0.055, 0.945, head, ha="left", fontsize=18, fontweight="bold", color=_INK)
    bits = [b for b in (opportunity_id,
                        f"{n_iterations:,} simulated paths" if n_iterations else "",
                        "rows share a scale across columns") if b]
    fig.text(0.055, 0.905, "     ·     ".join(bits), ha="left",
             fontsize=10.5, color=_MUTED)

    if save_path:
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        fig.savefig(save_path, dpi=150, facecolor=_PAGE)
        print(f"saved: {save_path}")
    if show:
        plt.show()
    return fig


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

"""Render the screening dataset as a clean, graphical HTML table.

One row per sample (a company at one timestep); company name + timestep pinned to
the left. Numeric fields render as a single-hue sequential heatmap (present =
coloured by magnitude, null = recedes to the surface), so the dataset's sparsity
is visible at a glance. Categorical/status fields render as labelled chips.

Run: python -m vcbrain.visualize  ->  screening_handover/dataset_view.html
"""

from __future__ import annotations

import html
import json
import os
from typing import Optional

from . import config

# --- column spec: (path, short label, kind) grouped by section --------------
# kinds: s100 (0-100 score) | u01 (0-1 signal) | intlog (log-scaled count) |
#        money | stage | sector | geo | mkt (market enum) | trend | flag
GROUPS = [
    ("screening", "Screening", [
        ("screening.founder_score_persistent", "Founder Score", "s100"),
        ("screening.founder_axis", "Founder axis", "s100"),
        ("screening.founder_axis_trend", "F.trend", "trend"),
        ("screening.market_axis", "Market", "mkt"),
        ("screening.market_axis_trend", "M.trend", "trend"),
        ("screening.idea_vs_market", "Idea·Mkt", "s100"),
        ("screening.idea_vs_market_trend", "I.trend", "trend"),
    ]),
    ("traction", "Traction", [
        ("traction.arr_usd", "ARR", "money"),
        ("traction.revenue_growth_rate_yoy", "Growth", "u01"),
        ("traction.customer_count", "Custs", "intlog"),
        ("traction.churn_rate_annual", "Churn", "u01"),
    ]),
    ("financials", "Financials", [
        ("financials.current_stage", "Stage", "stage"),
        ("financials.last_round_size_usd", "Last rnd", "money"),
        ("financials.total_funding_to_date_usd", "Total fund", "money"),
        ("financials.burn_rate_usd_monthly", "Burn", "money"),
        ("financials.runway_months", "Runway", "intlog"),
        ("financials.cash_balance_usd", "Cash", "money"),
    ]),
    ("team", "Team & defensibility", [
        ("team.team_size", "Team", "intlog"),
        ("team.founder_prior_exits", "Exits", "intlog"),
        ("team.founder_prior_startups", "Prior", "intlog"),
        ("team.single_founder_flag", "Solo", "flag"),
        ("team.technical_founder_flag", "Tech", "flag"),
        ("team.founder_industry_experience_years", "Ind.yrs", "intlog"),
        ("team.proprietary_score", "Propr.", "u01"),
        ("team.patent_count", "Patents", "intlog"),
        ("team.open_source_activity_score", "OSS", "u01"),
    ]),
    ("market", "Market", [
        ("market.tam_usd", "TAM", "money"),
        ("market.competitor_density", "Comp.dens", "u01"),
        ("market.funding_climate_index", "Climate", "u01"),
        ("market.sector", "Sector", "sector"),
        ("market.geography", "Geo", "geo"),
    ]),
    ("cold_start", "Cold-start", [
        ("cold_start.public_footprint_score", "Footprint", "u01"),
        ("cold_start.network_centrality", "Network", "u01"),
        ("cold_start.soft_skill_estimate", "Soft skill", "u01"),
    ]),
]

# Categorical group-accent hues (documented palette, slots 1-6; header accents
# only, always shown beside a text label -> identity never colour-alone).
GROUP_ACCENT = {
    "screening": "#2a78d6", "traction": "#008300", "financials": "#eda100",
    "team": "#1baf7a", "market": "#eb6834", "cold_start": "#4a3aa7",
}


def _get(rec: dict, path: str):
    cur = rec
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _norm(kind: str, v) -> Optional[float]:
    """Map a present value to [0,1] for the sequential heatmap intensity."""
    if v is None:
        return None
    try:
        if kind == "s100":
            return max(0.0, min(1.0, float(v) / 100.0))
        if kind == "u01":
            return max(0.0, min(1.0, float(v)))
        if kind == "intlog":
            import math
            return max(0.0, min(1.0, math.log10(max(float(v), 1)) / 4.0))
        if kind == "money":
            import math
            return max(0.0, min(1.0, (math.log10(max(float(v), 1)) - 4) / 8.0))
    except (TypeError, ValueError):
        return None
    return None


def _fmt(kind: str, v) -> str:
    if v is None:
        return "·"
    if kind == "s100":
        return f"{float(v):.0f}"
    if kind == "u01":
        return f"{float(v):.2f}"
    if kind == "intlog":
        return f"{int(v)}"
    if kind == "money":
        v = float(v)
        for unit, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
            if v >= div:
                return f"${v/div:.1f}{unit}"
        return f"${v:.0f}"
    if kind == "flag":
        return "yes" if v else "no"
    return html.escape(str(v))


_TREND = {"improving": ("▲", "good"), "declining": ("▼", "critical"),
          "stable": ("▬", "muted"), "unknown": ("·", "null")}
_MKT = {"bull": "good", "neutral": "warning", "bear": "critical", "unknown": "null"}


def _cell(kind: str, v) -> str:
    """Return one <td> for a value."""
    if v is None:
        return '<td class="c null" title="null (missing_fields)">·</td>'
    if kind == "trend":
        glyph, cls = _TREND.get(str(v), ("·", "null"))
        return f'<td class="c chip {cls}" title="{html.escape(str(v))}">{glyph}</td>'
    if kind == "mkt":
        cls = _MKT.get(str(v), "null")
        return f'<td class="c chip {cls}">{html.escape(str(v))}</td>'
    if kind in ("stage", "sector", "geo"):
        txt = html.escape(str(v)).replace("_", " ")
        return f'<td class="c txt" title="{html.escape(str(v))}">{txt}</td>'
    if kind == "flag":
        return f'<td class="c chip {"warning" if v else "muted"}">{_fmt(kind, v)}</td>'
    # numeric -> heatmap
    t = _norm(kind, v)
    if t is None:
        return f'<td class="c txt">{_fmt(kind, v)}</td>'
    pct = round(12 + t * 80)                       # 12%..92% mix toward --heat
    ink = "var(--on-heat)" if t > 0.55 else "var(--text-primary)"
    style = f"background:color-mix(in oklab, var(--heat) {pct}%, var(--surface-2));color:{ink}"
    return f'<td class="c heat" style="{style}" title="{_fmt(kind, v)}">{_fmt(kind, v)}</td>'


def _load(output_dir: str):
    def rd(fn):
        p = os.path.join(output_dir, fn)
        if not os.path.exists(p):
            return []
        return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]

    inf = rd("inference_requests.jsonl")
    tf = rd("training_features.jsonl")
    ev = rd("evidence_registry.jsonl")

    # recover names from the evidence registry ("YC directory: <name>")
    names: dict[str, str] = {}
    status: dict[str, str] = {}
    for e in ev:
        dn = e.get("document_name", "")
        if dn.startswith("YC directory: ") and e.get("startup_id") not in names:
            names[e["startup_id"]] = dn[len("YC directory: "):]
        ex = e.get("evidence_excerpt", "")
        if "status=" in ex and e.get("startup_id") not in status:
            try:
                status[e["startup_id"]] = ex.split("status=")[1].split(".")[0].split(",")[0].strip()
            except IndexError:
                pass
    return inf, tf, names, status


def build(output_dir: Optional[str] = None) -> str:
    output_dir = output_dir or config.OUTPUT_DIR
    inf, tf, names, status = _load(output_dir)

    rows = []
    for kind_lbl, recs in (("live", inf), ("hist", tf)):
        for r in recs:
            rows.append((kind_lbl, r))
    # sort by company name, then observation_date (timestep)
    rows.sort(key=lambda kr: (names.get(kr[1]["startup_id"], "zzz").lower(),
                              kr[1].get("observation_date", "")))

    # coverage per column across all rows
    flat_cols = [(path, kind) for (_, _, cols) in GROUPS for (path, _, kind) in cols]
    coverage = {}
    for path, _ in flat_cols:
        present = sum(1 for _, r in rows if _get(r, path) is not None)
        coverage[path] = present / len(rows) if rows else 0.0

    # --- build header ---
    grp_head = []
    col_head = []
    cov_head = []
    for gkey, glabel, cols in GROUPS:
        accent = GROUP_ACCENT[gkey]
        grp_head.append(f'<th colspan="{len(cols)}" class="grp" '
                        f'style="--accent:{accent}">{html.escape(glabel)}</th>')
        for path, lbl, kind in cols:
            col_head.append(f'<th class="col" title="{html.escape(path)}">{html.escape(lbl)}</th>')
            cov = coverage[path]
            w = round(cov * 100)
            cov_head.append(f'<th class="cov" title="{w}% present across {len(rows)} samples">'
                            f'<span class="covbar"><i style="width:{w}%"></i></span></th>')

    # --- build body ---
    body = []
    prev_company = None
    for kind_lbl, r in rows:
        sid = r["startup_id"]
        cname = names.get(sid, sid[:16])
        st = status.get(sid, "")
        ts = r.get("observation_date", "")
        badge = "live" if kind_lbl == "live" else "hist"
        newco = "newco" if cname != prev_company else ""
        prev_company = cname
        cells = [
            f'<th class="lh name {newco}" title="{html.escape(sid)}">{html.escape(cname)}'
            f'<span class="st st-{html.escape(st.lower())}">{html.escape(st)}</span></th>',
            f'<td class="lh ts"><span class="tag {badge}">{badge}</span>{html.escape(ts)}</td>',
        ]
        for path, kind in flat_cols:
            cells.append(_cell(kind, _get(r, path)))
        body.append("<tr>" + "".join(cells) + "</tr>")

    n_live = sum(1 for k, _ in rows if k == "live")
    n_hist = len(rows) - n_live
    return _PAGE.format(
        css=_CSS,
        n_rows=len(rows), n_live=n_live, n_hist=n_hist, n_cols=len(flat_cols),
        today=config.TODAY.isoformat(),
        grp_head="".join(grp_head), col_head="".join(col_head), cov_head="".join(cov_head),
        body="".join(body),
    )


_CSS = """
:root{--surface-1:#fcfcfb;--surface-2:#f3f2ef;--surface-3:#eceae5;
 --text-primary:#0b0b0b;--text-secondary:#52514e;--text-muted:#8a8985;--border:#dddbd4;
 --heat:#256abf;--on-heat:#fff;
 --good:#0ca30c;--warning:#c98500;--critical:#d03b3b;--muted:#8a8985;color-scheme:light}
:root[data-theme=dark]{--surface-1:#161615;--surface-2:#201f1d;--surface-3:#2b2a27;
 --text-primary:#fff;--text-secondary:#c3c2b7;--text-muted:#8f8e86;--border:#3a3934;
 --heat:#3987e5;--on-heat:#08131f;--warning:#fab219;--good:#0ca30c;--critical:#e66767;color-scheme:dark}
@media(prefers-color-scheme:dark){:root:not([data-theme=light]){--surface-1:#161615;--surface-2:#201f1d;
 --surface-3:#2b2a27;--text-primary:#fff;--text-secondary:#c3c2b7;--text-muted:#8f8e86;--border:#3a3934;
 --heat:#3987e5;--on-heat:#08131f;--warning:#fab219;--critical:#e66767;color-scheme:dark}}
*{box-sizing:border-box}
body{margin:0;background:var(--surface-1);color:var(--text-primary);
 font:13px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
header{padding:20px 24px 12px}
h1{margin:0 0 4px;font-size:18px;letter-spacing:-.01em}
.sub{color:var(--text-secondary);font-size:12.5px;max-width:70ch}
.legend{display:flex;gap:16px;flex-wrap:wrap;align-items:center;margin-top:12px;font-size:11.5px;color:var(--text-secondary)}
.legend .item{display:flex;align-items:center;gap:6px}
.sw{width:14px;height:14px;border-radius:3px;border:1px solid var(--border)}
.ramp{width:90px;height:12px;border-radius:3px;
 background:linear-gradient(90deg,var(--surface-2),var(--heat))}
.dot{width:10px;height:10px;border-radius:50%}
button{font:inherit;font-size:12px;padding:5px 11px;border:1px solid var(--border);
 border-radius:7px;background:var(--surface-2);color:var(--text-primary);cursor:pointer}
.wrap{overflow:auto;max-height:calc(100vh - 150px);border-top:1px solid var(--border);margin-top:10px}
table{border-collapse:separate;border-spacing:0;font-variant-numeric:tabular-nums}
th,td{white-space:nowrap}
thead th{position:sticky;background:var(--surface-1);z-index:3}
tr:first-child th.grp{top:0;height:26px;font-size:11px;text-transform:uppercase;
 letter-spacing:.04em;color:var(--text-secondary);text-align:left;padding:4px 8px;
 border-bottom:2px solid var(--accent)}
tr.colrow th{top:26px}
tr.covrow th{top:52px}
th.col{height:26px;padding:4px 8px;font-size:11px;font-weight:600;color:var(--text-secondary);
 text-align:right;border-bottom:1px solid var(--border)}
th.cov{padding:2px 8px 6px;border-bottom:1px solid var(--border)}
.covbar{display:block;height:4px;border-radius:2px;background:var(--surface-3);overflow:hidden}
.covbar i{display:block;height:100%;background:var(--heat)}
td.c,th.col{min-width:52px}
td.c{padding:5px 8px;text-align:right;border-bottom:1px solid var(--border);
 border-left:1px solid var(--surface-2);color:var(--text-primary)}
td.null{color:var(--text-muted);text-align:center;background:repeating-linear-gradient(
 45deg,transparent,transparent 4px,var(--surface-2) 4px,var(--surface-2) 5px)}
td.txt{text-align:left;color:var(--text-secondary);font-size:12px}
td.heat{font-weight:600}
.chip{text-align:center;font-size:11px;font-weight:600}
.chip.good{color:var(--good)} .chip.warning{color:var(--warning)}
.chip.critical{color:var(--critical)} .chip.muted{color:var(--muted)} .chip.null{color:var(--text-muted)}
/* pinned left columns */
th.lh,td.lh{position:sticky;background:var(--surface-1);z-index:2;border-bottom:1px solid var(--border)}
th.name{left:0;min-width:190px;max-width:190px;text-align:left;padding:6px 10px;font-weight:600;
 border-right:1px solid var(--border);white-space:normal}
td.ts{left:190px;min-width:132px;padding:6px 10px;text-align:left;color:var(--text-secondary);
 border-right:2px solid var(--border);font-size:12px}
thead th.lh{z-index:4;background:var(--surface-1)}
th.name.newco{border-top:2px solid var(--border)}
.st{display:block;font-size:10px;font-weight:600;margin-top:2px;color:var(--text-muted)}
.st-acquired,.st-public{color:var(--good)} .st-inactive{color:var(--critical)}
.tag{display:inline-block;font-size:9.5px;font-weight:700;text-transform:uppercase;letter-spacing:.03em;
 padding:1px 5px;border-radius:4px;margin-right:6px;vertical-align:1px}
.tag.live{background:color-mix(in oklab,var(--heat) 20%,var(--surface-2));color:var(--heat)}
.tag.hist{background:var(--surface-3);color:var(--text-secondary)}
"""

_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Screening dataset — {n_rows} samples</title><style>{css}</style></head>
<body>
<header>
 <h1>Screening dataset · {n_rows} samples × {n_cols} features</h1>
 <div class="sub">One row per company-timestep. Company &amp; timestep pinned left; each
 numeric field is a single-hue heatmap (darker = higher), null cells are hatched. Point-in-time
 <b>hist</b> snapshots (YC batch date) and <b>live</b> snapshots (as of {today}) are interleaved
 per company — {n_live} live, {n_hist} historical.</div>
 <div class="legend">
  <span class="item"><span class="ramp"></span> low → high</span>
  <span class="item"><span class="sw" style="background:repeating-linear-gradient(45deg,transparent,transparent 4px,var(--surface-3) 4px,var(--surface-3) 5px)"></span> null</span>
  <span class="item"><span class="dot" style="background:var(--good)"></span> bull / improving / good</span>
  <span class="item"><span class="dot" style="background:var(--warning)"></span> neutral</span>
  <span class="item"><span class="dot" style="background:var(--critical)"></span> bear / declining</span>
  <span class="item">bar under each header = % present</span>
  <button id="tg">Toggle theme</button>
 </div>
</header>
<div class="wrap"><table>
 <thead>
  <tr><th class="lh name">Company</th><td class="lh ts">Timestep</td>{grp_head}</tr>
  <tr class="colrow"><th class="lh name"></th><td class="lh ts"></td>{col_head}</tr>
  <tr class="covrow"><th class="lh name"></th><td class="lh ts">% present →</td>{cov_head}</tr>
 </thead>
 <tbody>{body}</tbody>
</table></div>
<script>
 const b=document.documentElement,t=document.getElementById('tg');
 t.onclick=()=>{{const d=b.getAttribute('data-theme');
  b.setAttribute('data-theme', d==='dark'?'light':(d==='light'?'dark':'dark'));}};
</script>
</body></html>"""


def main(argv=None) -> int:
    out = build()
    path = os.path.join(config.OUTPUT_DIR, "dataset_view.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(out)
    print(f"[vcbrain] wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

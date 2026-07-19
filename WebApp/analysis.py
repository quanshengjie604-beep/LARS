"""
analysis.py
===========

This is the *bridge point* between the web server and your real analysis code.

Right now it contains a small, self-contained placeholder so the WebApp runs
end-to-end out of the box. Later, you will replace the body of `run_analysis`
with a call into your existing LARS code (e.g. something from the `Scripts/`
package) and map its output into a plain dictionary.

Contract expected by `server.py`
--------------------------------
* Input:  a single dict of parameters coming from the HTML form (already parsed
          from JSON). Treat every value as untrusted user input.
* Output: a dict containing only standard, JSON-serialisable types
          (str, int, float, bool, None, list, dict). FastAPI turns this into
          the JSON the frontend reads.

Keep this function free of `print()` / file-writing side effects: *return* the
values instead, so the browser can display them.
"""

from __future__ import annotations

from typing import Any


def run_analysis(params: dict[str, Any]) -> dict[str, Any]:
    """Run the analysis for a given set of parameters and return results.

    Parameters
    ----------
    params:
        Dictionary of inputs sent from the frontend form. Expected keys for
        this placeholder: ``investment`` (float) and ``years`` (int). Unknown
        keys are ignored; missing keys fall back to sensible defaults.

    Returns
    -------
    dict
        JSON-serialisable results. The placeholder returns a trivial
        compound-growth projection so you can see the full request/response
        loop working before wiring in the real model.
    """
    # --- Parse & validate inputs -----------------------------------------
    # Values arriving from the browser are strings or numbers; coerce safely.
    try:
        investment = float(params.get("investment", 1000))
    except (TypeError, ValueError):
        investment = 1000.0

    try:
        years = int(params.get("years", 10))
    except (TypeError, ValueError):
        years = 10

    # Clamp to keep the placeholder well-behaved.
    years = max(0, min(years, 100))
    annual_rate = 0.08  # 8% placeholder growth rate

    # --- Placeholder "analysis" ------------------------------------------
    # Replace this block with a call into your real LARS analysis code, e.g.:
    #
    #     from Scripts.your_module import your_entrypoint
    #     result = your_entrypoint(investment=investment, years=years, ...)
    #     return {"moic": result.moic, "series": result.series, ...}
    #
    series = []
    value = investment
    for year in range(years + 1):
        series.append({"year": year, "value": round(value, 2)})
        value *= (1 + annual_rate)

    final_value = series[-1]["value"] if series else investment
    moic = round(final_value / investment, 3) if investment else 0.0

    return {
        "status": "ok",
        "inputs": {"investment": investment, "years": years},
        "summary": (
            f"An investment of {investment:,.2f} grows to "
            f"{final_value:,.2f} over {years} years "
            f"(MOIC {moic:.2f}x) at an assumed {annual_rate:.0%} annual rate."
        ),
        "final_value": final_value,
        "moic": moic,
        "series": series,
    }


if __name__ == "__main__":
    # Quick manual smoke test: `python analysis.py`
    import json

    print(json.dumps(run_analysis({"investment": 1000, "years": 10}), indent=2))

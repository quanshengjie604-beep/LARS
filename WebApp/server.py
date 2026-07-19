"""
server.py
=========

FastAPI web server that acts as the bridge between the browser frontend and
your Python analysis code.

Responsibilities
----------------
1. Serve the UI      -> GET  /              returns static/index.html
2. Expose an API     -> POST /api/analyze   runs analysis.run_analysis(...)
3. Auto-launch the browser one second after start-up.

Run it with either:

    python server.py

or (equivalently, with auto-reload during development):

    uvicorn server:app --reload
"""

from __future__ import annotations

import threading
import webbrowser
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from Run_MC import run_singular_profile_analysis
from intake import read_profile_submission
from enrich import EnrichmentError, enrich_profile_from_name

# --------------------------------------------------------------------------
# Paths & app setup
# --------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

HOST = "127.0.0.1"
PORT = 8000

app = FastAPI(title="LARS WebApp", version="0.1.0")


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------
@app.get("/")
async def index() -> FileResponse:
    """Serve the single-page frontend."""
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/analyze")
async def analyze() -> JSONResponse:
    """Run the Monte Carlo analysis and return the summary + dashboard image.

    Takes no parameters yet (the analysis currently uses the hard-coded demo
    profile). Returns a JSON object of the form::

        {"summary": {...}, "dashboard": "data:image/png;base64,..."}

    The simulation is CPU-bound and blocking, so we run it in a worker thread to
    avoid stalling the async event loop. Any exception is turned into a clean
    JSON error so the frontend can display it instead of receiving a raw 500.
    """
    try:
        result = await run_in_threadpool(run_singular_profile_analysis)
    except Exception as exc:  # noqa: BLE001 - surface any analysis error to the UI
        return JSONResponse(
            status_code=500,
            content={"status": "error", "detail": f"Analysis failed: {exc}"},
        )

    return JSONResponse(content={"status": "ok", **result})


@app.post("/api/profile")
async def profile(
    first_name: str = Form(""),
    last_name: str = Form(""),
    linkedin: str = Form(""),
    github: str = Form(""),
    website: str = Form(""),
    company_name: str = Form(""),
    company_sector: str = Form(""),
    email: str = Form(""),
    deck: UploadFile | None = File(None),
) -> JSONResponse:
    """Read in a questionnaire submission (text fields + optional PDF deck).

    Parses the multipart form, reads the uploaded deck's bytes, and hands
    everything to `read_profile_submission`. For now the read-in is the end of
    the line — nothing downstream happens with the data yet.
    """
    fields = {
        "first_name": first_name,
        "last_name": last_name,
        "linkedin": linkedin,
        "github": github,
        "website": website,
        "company_name": company_name,
        "company_sector": company_sector,
        "email": email,
    }

    try:
        deck_bytes = await deck.read() if deck is not None else None
        result = read_profile_submission(
            fields,
            deck_filename=deck.filename if deck is not None else None,
            deck_bytes=deck_bytes,
            deck_content_type=deck.content_type if deck is not None else None,
        )
    except Exception as exc:  # noqa: BLE001 - surface any read-in error to the UI
        return JSONResponse(
            status_code=500,
            content={"status": "error", "detail": f"Read-in failed: {exc}"},
        )

    return JSONResponse(content={"status": "ok", **result})


@app.post("/api/enrich")
async def enrich(
    name: str = Form(""),
    first_name: str = Form(""),
    last_name: str = Form(""),
    company_name: str = Form(""),
    linkedin: str = Form(""),
    website: str = Form(""),
) -> JSONResponse:
    """Research a founder from just their name and return an NGBoost request.

    Only ``name`` (or ``first_name``/``last_name``) is required; the other
    fields are optional hints that focus the web search. Returns::

        {"status": "ok",
         "inference_request": {...},   # schema-clean, ready for NGBoost
         "citations": [{"url", "title"}, ...],
         "model": "..."}

    The OpenAI call is blocking (network + tool use), so it runs in a worker
    thread to keep the event loop responsive.
    """
    full_name = name.strip() or f"{first_name} {last_name}".strip()
    if not full_name:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "detail": "A name is required."},
        )

    hints = {
        "Company": company_name,
        "LinkedIn": linkedin,
        "Website": website,
    }

    try:
        result = await run_in_threadpool(
            enrich_profile_from_name, full_name, hints
        )
    except EnrichmentError as exc:
        return JSONResponse(
            status_code=502,
            content={"status": "error", "detail": str(exc)},
        )
    except Exception as exc:  # noqa: BLE001 - surface any unexpected error to the UI
        return JSONResponse(
            status_code=500,
            content={"status": "error", "detail": f"Enrichment failed: {exc}"},
        )

    # Drop the (long) raw model text from the API payload; keep it server-side.
    result.pop("raw_text", None)
    return JSONResponse(content={"status": "ok", **result})


# Mount the static directory last so it doesn't shadow the routes above.
# Files placed in static/ (css, js, images) are served under /static/...
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# --------------------------------------------------------------------------
# Auto-launch browser
# --------------------------------------------------------------------------
def _open_browser() -> None:
    """Open the app in the default browser shortly after the server starts."""
    webbrowser.open(f"http://{HOST}:{PORT}")


def main() -> None:
    import uvicorn

    # Fire the browser open ~1s after start-up, on a background timer so it
    # doesn't block the server from binding to the port.
    threading.Timer(1.0, _open_browser).start()

    # reload=False because the Timer/webbrowser trick doesn't play well with
    # uvicorn's reloader (which spawns a child process).
    uvicorn.run(app, host=HOST, port=PORT, reload=False)


if __name__ == "__main__":
    main()

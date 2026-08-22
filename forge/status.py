"""
forge/status.py -- the status endpoint.

api.py is Damir's file and does not exist yet, so this ships as a router he
mounts in one line rather than as a second FastAPI app competing with his:

    from forge.status import router
    app.include_router(router)

Kept separate on purpose -- two people creating api.py is how the 11:00 merge
goes wrong.
"""
from __future__ import annotations

import json
import os

from fastapi import APIRouter, HTTPException, Response

from forge import config, llm

router = APIRouter()


@router.get("/api/status")
def status() -> dict:
    """What the factory has spent, and on what."""
    return llm.budget_status()


def _suppressed_ids() -> set:
    """Findings a human dismissed with a written reason. Tolerant of whichever
    shape store.py ends up with."""
    from forge import store

    getter = getattr(store, "suppressed_ids", None)
    if callable(getter):
        try:
            return set(getter())
        except Exception:
            pass
    return set(getattr(store, "_SUPPRESSED", {}) or {})


@router.get("/api/findings")
def findings() -> dict:
    """What the factory currently knows, from the durable catalog.

    NOT from the most recent audit object: a fix run's closing audit covers one
    route, and reading that would make the dashboard flicker down to a single
    page every time a run finished. The catalog is the union of every audit,
    with findings marked open, closed or suppressed.
    """
    from forge import store
    from forge.models import GRADE_VALUE, grade_for

    last = store.last_audit()
    rows = store.all_findings()
    if last is None and not rows:
        # Never audited is NOT the same as clean, and the page must not show
        # green for it. A judge reading green means "checked and safe".
        return {"audited": False, "routes": [], "findings": [], "totals": {}, "worst_grade": None}

    open_rows = [r for r in rows if r.get("status") == "open"]
    shown = [r for r in rows if r.get("status") in ("open", "suppressed")]

    routes = []
    for route in sorted({r.get("route") for r in shown if r.get("route")}):
        mine = [r for r in open_rows if r.get("route") == route]
        counts = {"HIGH": 0, "MED": 0, "LOW": 0}
        for row in mine:
            key = (row.get("severity") or "LOW").upper()
            counts[key] = counts.get(key, 0) + 1
        grade = grade_for(mine)
        routes.append({"route": route, "grade": grade, "grade_value": GRADE_VALUE.get(grade, 0), "counts": counts})

    totals = {"HIGH": 0, "MED": 0, "LOW": 0}
    for row in open_rows:
        key = (row.get("severity") or "LOW").upper()
        totals[key] = totals.get(key, 0) + 1

    order = {"HIGH": 0, "MED": 1, "LOW": 2}
    return {
        "audited": True,
        "base_url": (last or {}).get("base_url", ""),
        "reachable": (last or {}).get("reachable", True),
        "duration_ms": (last or {}).get("duration_ms", 0),
        "audited_at": (last or {}).get("at"),
        "worst_grade": min([r["grade"] for r in routes], key=lambda g: GRADE_VALUE.get(g, 0)) if routes else "gold",
        "routes": sorted(routes, key=lambda r: r["grade_value"]),
        "findings": sorted(shown, key=lambda r: (order.get((r.get("severity") or "").upper(), 3), r.get("route") or "")),
        "totals": totals,
        "open_count": len(open_rows),
        "suppressed_count": len([r for r in rows if r.get("status") == "suppressed"]),
    }


# --------------------------------------------------------------------------
# the operator console
# --------------------------------------------------------------------------
CONSOLE_DIR = config.REPO_ROOT / "forge" / "console"

#: The one place the console's API base is configured. Empty means same origin,
#: which is correct when forge-control serves the console itself.
CONSOLE_API_BASE = os.getenv("FORGE_CONSOLE_API_BASE", "")

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".png": "image/png",
    ".webmanifest": "application/manifest+json",
}


def _console_file(relative: str) -> Response:
    """Serve a file from forge/console, and nothing outside it.

    Served through a router rather than app.mount so that forge/api.py needs
    exactly one new endpoint and no other change.
    """
    target = (CONSOLE_DIR / relative).resolve()
    try:
        target.relative_to(CONSOLE_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=404, detail="not found")
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"{relative} is not in forge/console")

    body = target.read_bytes()

    # config.js is the console's single configuration point. The API base is
    # appended from one env var so a demo machine can point the console at a
    # forge-control elsewhere without editing a checked-in file.
    if relative == "config.js" and CONSOLE_API_BASE:
        body += (
            "\n// injected by forge-control from FORGE_CONSOLE_API_BASE\n"
            f'window.FORGE_CONFIG.apiBase = {json.dumps(CONSOLE_API_BASE)};\n'
        ).encode("utf-8")

    return Response(
        content=body,
        media_type=_CONTENT_TYPES.get(target.suffix, "application/octet-stream"),
        headers={"Cache-Control": "no-store"},
    )


#: index.html is the landing page; dashboard.html is the operator console.
LANDING_PAGE = "index.html"
CONSOLE_PAGE = "dashboard.html"

#: Files the landing page and console load. Each gets its OWN route at the
#: repo root. Deliberately NOT app.mount("/", StaticFiles(...)): a mount at
#: root matches before every API route, and one added earlier today made
#: /health, /api/runs and every other endpoint return 404 with no error
#: anywhere. An explicit list cannot shadow an endpoint.
ROOT_ASSETS = (
    "app.js", "auth.js", "chat-input.js", "config.js", "demo-data.js",
    "landing.js", "dashboard.html", "index.html",
)


@router.get("/")
def landing() -> Response:
    """The landing page, at the address a browser goes to by default."""
    return _console_file(LANDING_PAGE)


@router.get("/console")
def console_index() -> Response:
    return _console_file(CONSOLE_PAGE)


@router.get("/console/{path:path}")
def console_asset(path: str) -> Response:
    return _console_file(path or CONSOLE_PAGE)


def _register_root_assets() -> None:
    """Serve each console asset from the root too.

    A page served at /console has a base URL of /, so its relative script tags
    resolve to /app.js rather than /console/app.js. Registering both keeps the
    console working at either address without editing its markup.
    """
    for name in ROOT_ASSETS:
        router.add_api_route(
            "/" + name,
            (lambda filename: (lambda: _console_file(filename)))(name),
            methods=["GET"],
            name="asset_" + name.replace(".", "_"),
            include_in_schema=False,
        )


_register_root_assets()

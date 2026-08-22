"""
pulse/routes/security.py -- the screen the judge watches.

    GET /security

Mount it from pulse/main.py in one line:

    from pulse.routes import security
    app.include_router(security.router)

Pulse and forge-control are separate processes, so this reads the factory's
findings over HTTP rather than importing its state. If the factory is not
reachable, the page says exactly that instead of rendering an empty list --
"no findings" and "no answer" look identical on a screen and mean opposite
things.

OWNER: ROHIT (section 06 -- Pulse's UI and the findings view).
"""
from __future__ import annotations

import os
from pathlib import Path

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()

FORGE_CONTROL_URL = os.getenv("FORGE_CONTROL_URL", "http://localhost:8000")
REFRESH_SECONDS = int(os.getenv("SECURITY_PAGE_REFRESH", "10"))

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


def _fetch() -> dict:
    """Ask the factory what the last audit saw."""
    try:
        response = httpx.get(f"{FORGE_CONTROL_URL.rstrip('/')}/api/findings", timeout=4.0)
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        return {"unreachable": True, "error": f"{type(exc).__name__}: {exc}", "audited": False}


def _state(data: dict) -> str:
    """The one word the whole page is coloured by."""
    if data.get("unreachable"):
        return "unreachable"
    if not data.get("audited"):
        return "pending"
    if not data.get("reachable", True):
        return "outage"
    totals = data.get("totals") or {}
    if totals.get("HIGH"):
        return "critical"
    if totals.get("MED") or totals.get("LOW"):
        return "warning"
    return "clean"


HEADLINE = {
    "clean": ("ALL CLEAR", "Every check passed on the last audit."),
    "critical": ("ACTION NEEDED", "High severity findings are open right now."),
    "warning": ("MINOR ISSUES", "No high severity findings. Lower severity work is open."),
    "pending": ("NOT YET AUDITED", "No audit has run. This is not the same as being clean."),
    "outage": ("TARGET DOWN", "The app served nothing, so every check failed for one reason."),
    "unreachable": ("FACTORY UNREACHABLE", "Cannot reach forge-control, so this page is not showing live data."),
}


@router.get("/security", response_class=HTMLResponse)
def security(request: Request):
    data = _fetch()
    state = _state(data)
    headline, subtitle = HEADLINE[state]
    return _TEMPLATES.TemplateResponse(
        request,
        "security.html",
        {
            "data": data,
            "state": state,
            "headline": headline,
            "subtitle": subtitle,
            "refresh": REFRESH_SECONDS,
            "totals": data.get("totals") or {},
            "routes": data.get("routes") or [],
            "findings": data.get("findings") or [],
        },
    )

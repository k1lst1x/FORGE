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

from fastapi import APIRouter

from forge import llm

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
    """What the last audit saw. This is what Pulse's /security page renders.

    Served from the most recent audit rather than by auditing on request: a page
    load must not be able to trigger seventeen HTTP checks against the app that
    is serving it.
    """
    from forge import audit
    from forge.models import GRADE_VALUE

    result = audit.last_result()
    if result is None:
        # Never audited is NOT the same as clean, and the page must not show
        # green for it. A judge reading green means "checked and safe".
        return {"audited": False, "routes": [], "findings": [], "totals": {}, "worst_grade": None}

    suppressed = _suppressed_ids()
    rows = []
    for finding in result.findings:
        rows.append({
            **finding,
            "status": "suppressed" if finding.get("finding_id") in suppressed else "open",
        })
    open_rows = [r for r in rows if r["status"] == "open"]

    routes = []
    for route, grade in (result.grades or {}).items():
        counts = {"HIGH": 0, "MED": 0, "LOW": 0}
        for row in open_rows:
            if row.get("route") == route:
                counts[(row.get("severity") or "LOW").upper()] = counts.get((row.get("severity") or "LOW").upper(), 0) + 1
        routes.append({"route": route, "grade": grade, "grade_value": GRADE_VALUE.get(grade, 0), "counts": counts})

    totals = {"HIGH": 0, "MED": 0, "LOW": 0}
    for row in open_rows:
        key = (row.get("severity") or "LOW").upper()
        totals[key] = totals.get(key, 0) + 1

    return {
        "audited": True,
        "base_url": result.base_url,
        "reachable": result.reachable,
        "duration_ms": result.duration_ms,
        "worst_grade": result.worst_grade,
        "routes": sorted(routes, key=lambda r: r["grade_value"]),
        "findings": sorted(rows, key=lambda r: ({"HIGH": 0, "MED": 1, "LOW": 2}.get((r.get("severity") or "").upper(), 3), r.get("route") or "")),
        "totals": totals,
        "open_count": len(open_rows),
        "suppressed_count": len(rows) - len(open_rows),
    }

"""
forge/api.py -- forge-control. The factory as a running service.

This did not exist, which is why nothing was reachable and everything looked
simulated. It is the process that owns the scheduler, the intake endpoints, the
human approval queue and the read APIs the security page renders from.

    python -m forge.api          (or: make up)

Every intake returns immediately and processes in a background task: a sender
that waits on a full factory run will time out and retry, and we get duplicate
runs for one event.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from forge import approvals, config, scheduler, store
from forge.intake import router as intake_router
from forge.status import router as status_router



logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
log = logging.getLogger("forge.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    missing = config.missing()
    for name, absent in missing.items():
        if absent:
            log.warning("NOT CONFIGURED: %s -- that part of the loop runs degraded", name)
    task = asyncio.create_task(scheduler.loop())
    log.info("forge-control up on port %s, auditing %s", config.FORGE_CONTROL_PORT, config.PULSE_BASE_URL)
    yield
    task.cancel()


app = FastAPI(title="forge-control", lifespan=lifespan)

app.include_router(intake_router)
app.include_router(status_router)


class FindingIn(BaseModel):
    finding: dict
    trigger: str | None = None


def _run_finding(finding: dict) -> None:
    from forge import engine

    try:
        engine.run_from_finding(finding)
    except Exception:
        log.exception("finding run failed")


@app.post("/intake/finding")
async def intake_finding(payload: dict, background: BackgroundTasks):
    """Findings arrive here from the scheduler and from SigNoz alerts.

    SigNoz sends Alertmanager-shaped payloads that may carry a LIST of grouped
    alerts, so both shapes are handled.
    """
    findings = []
    if "finding" in payload:
        findings = [payload["finding"]]
    elif "alerts" in payload:
        for alert in payload.get("alerts") or []:
            labels = alert.get("labels") or {}
            findings.append({
                "finding_id": labels.get("finding_id") or f"alert_{labels.get('alertname', 'signoz')}",
                "check_id": labels.get("check_id", "S1"),
                "severity": labels.get("severity", "HIGH").upper(),
                "route": labels.get("route", "/"),
                "title": (alert.get("annotations") or {}).get("summary", "SigNoz alert"),
                "evidence": (alert.get("annotations") or {}).get("description", ""),
                "suggested_fix_hint": "",
            })
    if not findings:
        raise HTTPException(status_code=422, detail="no finding in payload")

    for finding in findings:
        background.add_task(_run_finding, finding)
    return {"accepted": len(findings)}


@app.get("/health")
def health():
    audit_state = scheduler.state()
    from forge import llm, telemetry

    return {
        "ok": True,
        "pulse": config.PULSE_BASE_URL,
        "telemetry": {
            "exporting_to_signoz": telemetry.exporting(),
            "region": config.SIGNOZ_REGION,
        },
        "llm": {
            "provider": llm.provider(),
            "spend_usd": llm.budget_status()["spend_usd"],
            "budget_usd": llm.budget_status()["budget_usd"],
        },
        "scheduler": audit_state,
        "open_findings": len(store.open_findings()),
        "pending_approvals": len(approvals.pending()),
        "not_configured": [k for k, v in config.missing().items() if v],
    }


@app.get("/api/runs")
def runs(limit: int = 50):
    return {"runs": store.list_runs(limit)}


#: A run is finished once it reaches the closing audit or lands a final status.
TERMINAL_STAGES = {"AUDIT"}
TERMINAL_STATUSES = {"done", "failed", "escalated", "rejected"}


def _is_terminal(run: dict) -> bool:
    return (run.get("stage") in TERMINAL_STAGES) or (run.get("status") in TERMINAL_STATUSES)


@app.get("/api/runs/current")
def current_run():
    """The run in flight, or an explicit null. ALWAYS 200 -- never 404.

    Declared above /api/runs/{run_id} on purpose: FastAPI matches in
    declaration order, so the parameterised route would otherwise swallow
    "current" as a run_id and 404.

    200-with-null rather than 404 because a client cannot tell a 404 meaning
    "nothing is happening" from a failed fetch meaning "the backend is down".
    The console fell back to demo data on both, so an idle factory rendered
    identically to a dead one. An idle factory now says it is idle.

    Reads the same store /api/runs does; adds no state of its own.
    """
    for run in store.list_runs(50):
        if not _is_terminal(run):
            return {"run": run}
    return {"run": None}


@app.get("/api/runs/{run_id}")
def run_detail(run_id: str):
    found = store.get_run(run_id)
    if not found:
        raise HTTPException(status_code=404, detail="unknown run")
    return found


@app.post("/audit/run")
async def audit_now():
    """Force an audit immediately -- needed for the demo."""
    return await scheduler.run_once(trigger="manual")


@app.get("/api/approvals")
def list_approvals():
    return {"pending": approvals.pending()}


@app.post("/api/approvals/{approval_id}/{decision}")
def decide(approval_id: str, decision: str, who: str = "human"):
    if decision not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="decision must be approve or reject")
    if not approvals.decide(approval_id, decision == "approve", who):
        raise HTTPException(status_code=404, detail="unknown approval")
    return {"approval_id": approval_id, "decision": decision}


@app.get("/approve", response_class=HTMLResponse)
def console():
    """A minimal console so a human can approve without Port being wired."""
    pending = approvals.pending()
    rows = "".join(
        f"<tr><td>{p['run_id']}</td><td>{p.get('classification','')}</td>"
        f"<td>{p.get('title','')[:70]}</td><td>{p.get('pr_url','') or ''}</td>"
        f"<td><button onclick=\"d('{p['approval_id']}','approve')\">Approve</button> "
        f"<button onclick=\"d('{p['approval_id']}','reject')\">Reject</button></td></tr>"
        for p in pending
    ) or "<tr><td colspan=5>Nothing waiting on a human.</td></tr>"
    return f"""<!doctype html><html><head><title>forge-control</title>
<meta name="description" content="FORGE control console"><meta http-equiv="refresh" content="5">
<style>body{{background:#0b1020;color:#f8fafc;font-family:system-ui;padding:2rem;font-size:18px}}
table{{width:100%;border-collapse:collapse}}td,th{{padding:.7rem;border-bottom:1px solid #2a3350;text-align:left}}
button{{font-size:1rem;padding:.4rem .9rem;margin-right:.4rem;cursor:pointer}}</style></head>
<body><h1>forge-control</h1>
<p>{len(pending)} change(s) waiting for a human. Auditing {config.PULSE_BASE_URL} every {config.AUDIT_INTERVAL_SECONDS}s.</p>
<table><tr><th>Run</th><th>Triage</th><th>Title</th><th>PR</th><th>Decision</th></tr>{rows}</table>
<script>function d(id,x){{fetch('/api/approvals/'+id+'/'+x,{{method:'POST'}}).then(()=>location.reload())}}</script>
</body></html>"""


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=config.FORGE_CONTROL_PORT, log_level="info")


if __name__ == "__main__":
    main()

"""
app/api/console.py -- a bridge from the operator console to the factory API.

WHY THIS EXISTS
--------------------------------------------------------------------------
frontend/public/console/app.js was written against forge-control, which serves
an /api/* surface:

    /api/status  /api/runs  /api/runs/current  /api/findings  /api/catalog
    /api/brief   /audit/run  /api/approvals

app/main.py serves a completely different namespace -- /factory/* -- so every
one of those calls 404s, and the console falls back to demo-data.js with the
"DEMO DATA -- forge-control not reachable" banner. That banner is about MISSING
ROUTES, not about missing credentials: no API key can conjure a route that was
never mounted.

forge-control's own implementation lives in app/api.py and cannot be imported
at all, because the package app/api/ (this directory) shadows that module. Until
that collision is resolved, this router translates the console's vocabulary onto
app.factory.store -- the same data the React operator app renders, so both
front ends finally show the same factory.

THIS IS GLUE, AND IT IS MEANT TO BE DELETED.
When the real forge-control is restored, drop this file and remove the two
lines that mount it in main.py. Nothing else imports it.

NO AUTH ON PURPOSE
--------------------------------------------------------------------------
/factory/* sits behind require_auth and takes a FORGE bearer token. The console
authenticates against SUPABASE and holds a Supabase JWT, which this backend has
no way to validate -- so requiring auth here would 401 every poll and put the
console straight back into demo mode. These routes are therefore open, which is
safe only because the server binds 127.0.0.1. Do not expose this port.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from app.factory import engine, observability, scheduler, store
from app.factory.models import FactoryRun, FactoryRunDetail, FactoryRunStatus

logger = logging.getLogger(__name__)

router = APIRouter(tags=["console-bridge"])

#: The console's pipeline is fixed at these eight, uppercase. They line up
#: one-to-one with engine.STAGES.
_STAGE_ORDER = ("INTAKE", "CONTEXT", "TRIAGE", "PLAN", "ACT", "VERIFY", "GATE", "RELEASE")

#: A run in one of these is finished; anything else is still "current".
#: awaiting_human is deliberately NOT terminal -- the run is parked at the gate,
#: which is the single most interesting thing the console can show.
_TERMINAL = {FactoryRunStatus.released, FactoryRunStatus.escalated, FactoryRunStatus.failed}

#: FactoryStepStatus -> the four states pipelineNode() knows how to draw.
#: There is no "failed" glyph in the console, so a failed step renders as done
#: and the failure surfaces through run.status / run.outcome instead.
_STEP_STATUS = {
    "completed": "done",
    "failed": "done",
    "running": "active",
    "skipped": "skipped",
    "pending": "pending",
}


def _parse(ts: str | None) -> datetime | None:
    """SQLite writes 'YYYY-MM-DD HH:MM:SS' with no zone. Read it as UTC."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts).replace(tzinfo=UTC)
    except ValueError:
        return None


def _iso(ts: str | None) -> str | None:
    parsed = _parse(ts)
    return parsed.isoformat() if parsed else None


def _parse_iso(value: object) -> datetime | None:
    """Parse an offset-aware ISO timestamp, as the scheduler writes them."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _stages_for(detail: FactoryRunDetail) -> dict[str, dict[str, Any]]:
    """Per-stage detail built from the run's real step timings.

    Sent explicitly rather than letting the console's deriveStages() guess from
    a single `stage` field, because we have the actual durations and it draws
    them under each node.
    """
    stages: dict[str, dict[str, Any]] = {name: {"status": "pending"} for name in _STAGE_ORDER}
    for step in detail.steps:
        name = step.name.upper()
        if name not in stages:
            continue
        started, completed = _parse(step.started_at), _parse(step.completed_at)
        duration = (completed - started).total_seconds() * 1000 if started and completed else None
        stages[name] = {
            "status": _STEP_STATUS.get(str(step.status), "pending"),
            "duration_ms": round(duration, 1) if duration is not None else None,
        }

    # A run parked at the human gate is sitting ON the gate, not past it.
    # The engine's release step does run, but only to record that release is
    # paused -- so reporting GATE done / RELEASE done is a lie in two ways:
    # renderGate() only draws the "Waiting for human approval" panel when
    # GATE is active, so the operator saw a finished-looking pipeline with a
    # forever-climbing clock and nothing saying what it was waiting for.
    if detail.status == FactoryRunStatus.awaiting_human:
        stages["GATE"]["status"] = "active"
        stages["RELEASE"] = {"status": "pending"}
    return stages


def _current_stage(detail: FactoryRunDetail) -> str:
    """Where the run actually IS, uppercase.

    Not simply the furthest step executed: a run awaiting a human is stopped at
    GATE even though the release step ran, because all that step did was record
    that release is paused.
    """
    if detail.status == FactoryRunStatus.awaiting_human:
        return "GATE"
    reached = [s.name.upper() for s in detail.steps if s.name.upper() in _STAGE_ORDER]
    return reached[-1] if reached else "INTAKE"


def _run_payload(detail: FactoryRunDetail) -> dict[str, Any]:
    """One run in the shape normRun() expects."""
    latest_verify = detail.verify[-1] if detail.verify else None
    finding = detail.findings[0] if detail.findings else None

    return {
        "run_id": detail.id,
        "intake": str(detail.intake),
        "trigger": detail.trigger,
        "title": detail.title,
        "stage": _current_stage(detail),
        "status": str(detail.status),
        "attempts": len(detail.verify) or 1,
        "trace_id": detail.trace_id,
        "created_at": _iso(detail.created_at),
        "finished_at": _iso(detail.updated_at) if detail.status in _TERMINAL else None,
        "outcome": detail.outcome,
        "classification": str(detail.classification) if detail.classification else None,
        "brief_text": detail.brief,
        "branch": detail.branch,
        "pr_url": detail.pr_url,
        "approval_id": f"approval-{detail.id}"
        if detail.status == FactoryRunStatus.awaiting_human
        else None,
        "finding": {
            "finding_id": finding.id,
            "check_id": finding.check_id,
            "severity": str(finding.severity),
            "route": finding.route,
            "evidence": finding.evidence,
            "title": finding.title,
        }
        if finding
        else None,
        "verify": {
            "tests_passed": latest_verify.tests_passed,
            "closed": latest_verify.findings_closed,
            "introduced": latest_verify.findings_introduced,
            "evidence": latest_verify.tests_output,
        }
        if latest_verify
        else None,
        "stages": _stages_for(detail),
    }


def _history_payload(run: FactoryRun) -> dict[str, Any]:
    """One run in the flatter shape normHistory() expects."""
    return {
        "run_id": run.id,
        "created_at": _iso(run.created_at),
        "intake": str(run.intake),
        "trigger": run.trigger,
        "title": run.title,
        "classification": str(run.classification) if run.classification else None,
        "outcome": run.outcome or str(run.status),
        "attempts": len(run.verify) or 1,
        "trace_id": run.trace_id,
        "pr_url": run.pr_url,
    }


@router.get("/api/status")
def console_status() -> dict[str, Any]:
    """The console's liveness probe.

    This endpoint answering is the ONLY thing that takes the console out of
    demo mode -- pollStatus() calls enterDemo() when it throws. Every other
    poll degrades on its own without flipping the banner.
    """
    runs = store.list_runs()
    findings = store.list_findings()
    snapshot = observability.snapshot()
    sched = scheduler.status()

    severity = {"HIGH": 0, "MED": 0, "LOW": 0}
    for finding in findings:
        key = str(finding.severity).upper()
        severity[key] = severity.get(key, 0) + 1

    now = datetime.now(UTC)
    today = now.date()
    runs_today = sum(1 for r in runs if (_parse(r.created_at) or now).date() == today)

    # Twelve one-hour buckets, oldest first -- the sparkline in the status bar.
    buckets = [0] * 12
    for run in runs:
        created = _parse(run.created_at)
        if not created:
            continue
        hours_ago = int((now - created).total_seconds() // 3600)
        if 0 <= hours_ago < 12:
            buckets[11 - hours_ago] += 1

    interval = int(sched.get("interval_seconds") or 60)

    # None when the scheduler is stopped, because then there is no next audit.
    # Reporting the bare interval instead pinned the header at a constant 60:
    # the console ticks its own countdown down but re-reads this every 3s, so
    # it fell to 0:57 and snapped back to 1:00 forever. mmss(null) renders
    # "--:--", and the local tick skips null, so an idle scheduler now looks
    # idle instead of looking broken.
    next_audit: int | None = None
    if sched.get("running"):
        next_audit = interval
        started = _parse_iso(sched.get("last_started_at"))
        if started:
            next_audit = max(0, int(interval - (now - started).total_seconds()))

    return {
        "scheduler": "healthy" if sched.get("running") else "down",
        "next_audit_seconds": next_audit,
        "audit_interval_seconds": interval,
        "runs_today": runs_today,
        "severity": severity,
        "grades": {c["route"]: str(c["grade"]).lower() for c in snapshot["scorecards"]},
        "runs_per_hour": buckets,
    }


@router.get("/api/runs/current")
def console_current_run() -> dict[str, Any]:
    """The run in flight, or an explicit null. ALWAYS 200, never 404.

    A client cannot tell a 404 meaning "nothing is happening" from a failed
    fetch meaning "the backend is down" -- and the console treats both as a
    reason to show demo data. An idle factory has to be able to say it is idle.
    """
    for run in store.list_runs():
        if run.status not in _TERMINAL:
            return {"run": _run_payload(store.get_run_detail(run.id))}
    return {"run": None}


@router.get("/api/runs")
def console_runs(limit: int = 20) -> dict[str, Any]:
    return {"runs": [_history_payload(r) for r in store.list_runs()[:limit]]}


@router.get("/api/findings")
def console_findings() -> dict[str, Any]:
    return {
        "findings": [
            {
                "finding_id": f.id,
                "check_id": f.check_id,
                "severity": str(f.severity),
                "route": f.route,
                "title": f.title,
                "status": "open",
                "occurrences": f.occurrences,
                "evidence": f.evidence,
                "suggested_fix_hint": f.suggested_fix_hint,
                "first_seen": _iso(f.created_at),
                "run_id": f.run_id,
            }
            for f in store.list_findings()
        ]
    }


@router.get("/api/catalog")
def console_catalog() -> dict[str, Any]:
    """The page catalog, derived from the observability scorecards."""
    snapshot = observability.snapshot()
    return {
        "pages": [
            {
                "route": card["route"],
                "title": f"Pulse — {card['route']}",
                "grade": str(card["grade"]).lower(),
                "high": card["open_findings_high"],
                "med": card["open_findings_med"],
                "low": card["open_findings_low"],
                "last_audit": snapshot["generated_at"],
                "page_id": card["route"],
            }
            for card in snapshot["scorecards"]
        ]
    }


class ConsoleBrief(BaseModel):
    description: str = Field(min_length=1, max_length=4000)
    title: str | None = None
    priority: str | None = None


def _execute_in_background(run_id: str) -> None:
    """A failed run must not take the service with it."""
    try:
        engine.resume_planned_run(run_id)
    except Exception:
        logger.exception("background run %s failed", run_id)


@router.post("/api/brief")
def console_brief(payload: ConsoleBrief, background_tasks: BackgroundTasks) -> dict[str, Any]:
    """Accept a brief and return BEFORE the run finishes.

    Running the eight stages inline took 7.3s against the console's 8s abort,
    so a submission failed on any network jitter -- and failed misleadingly:
    the browser gave up but the server carried the run to completion, so
    "Could not submit" left a finished run behind. Retrying then produced a
    second one. Exactly what forge-control's own intake docstring warned about.

    The run is RECORDED inside the request, so the id is real and the console's
    next poll finds it, and EXECUTED in a background task.
    """
    run_id = engine.create_planned_run(brief=payload.description, trigger="console")
    background_tasks.add_task(_execute_in_background, run_id)
    return {"accepted": True, "run_id": run_id}


def _audit_in_background() -> None:
    """Sync on purpose, so Starlette runs it in a worker thread.

    scheduler.run_once() is declared async but its body is blocking -- it calls
    straight into the engine, which does subprocess and sync-httpx work. As an
    async background task it ran ON the event loop and stalled every other
    response for the length of the run: POST /audit/run took 17s to return a
    body it had already produced. In a thread it returns in milliseconds.
    """
    try:
        asyncio.run(scheduler.run_once())
    except Exception:
        logger.exception("background audit failed")


@router.post("/audit/run")
def console_audit_now(background_tasks: BackgroundTasks) -> dict[str, Any]:
    """Same contract as the brief intake: return now, audit in the background."""
    background_tasks.add_task(_audit_in_background)
    return {"accepted": True}


@router.get("/api/approvals")
def console_approvals() -> dict[str, Any]:
    return {
        "pending": [
            {
                "approval_id": f"approval-{run.id}",
                "run_id": run.id,
                "title": run.title,
                "classification": str(run.classification) if run.classification else "",
                "pr_url": run.pr_url or "",
            }
            for run in store.list_runs()
            if run.status == FactoryRunStatus.awaiting_human
        ]
    }


@router.post("/api/approvals/{approval_id}/{decision}")
def console_decide(approval_id: str, decision: str) -> dict[str, Any]:
    if decision not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="decision must be approve or reject")

    run_id = approval_id.removeprefix("approval-")
    try:
        store.get_run(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="unknown approval") from exc

    if decision == "approve":
        store.update_run(
            run_id,
            status=FactoryRunStatus.released,
            next_gate="Approved from the operator console.",
            outcome="approved_by_human",
        )
    else:
        store.update_run(
            run_id,
            status=FactoryRunStatus.escalated,
            next_gate="Rejected from the operator console; escalation required.",
            outcome="rejected_by_human",
        )
    return {"approval_id": approval_id, "decision": decision}

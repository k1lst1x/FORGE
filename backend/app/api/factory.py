from fastapi import APIRouter, HTTPException

from app.factory import engine, scheduler, store
from app.factory.models import (
    FactoryRun,
    FactoryRunCreate,
    FactoryRunDetail,
    FactoryRunStatus,
    Finding,
)

router = APIRouter(prefix="/factory", tags=["factory"])


@router.get("/runs", response_model=list[FactoryRun])
def list_runs() -> list[FactoryRun]:
    return store.list_runs()


@router.post("/runs", response_model=FactoryRunDetail, status_code=201)
def create_run(payload: FactoryRunCreate) -> FactoryRunDetail:
    if payload.auto_start:
        change_request = engine.run_from_brief(brief=payload.brief, trigger=payload.trigger)
        return store.get_run_detail(change_request.run_id)

    run_id = engine.create_planned_run(brief=payload.brief, trigger=payload.trigger)
    return store.get_run_detail(run_id)


@router.get("/runs/{run_id}", response_model=FactoryRunDetail)
def get_run(run_id: str) -> FactoryRunDetail:
    try:
        return store.get_run_detail(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Factory run not found") from exc


@router.get("/findings", response_model=list[Finding])
def list_findings() -> list[Finding]:
    return store.list_findings()


@router.post("/runs/{run_id}/approve", response_model=FactoryRunDetail)
def approve_run(run_id: str) -> FactoryRunDetail:
    try:
        run = store.get_run(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Factory run not found") from exc

    merged = False
    if run.pr_url:
        from app.factory import vcs

        merged = vcs.merge_pr(run.pr_url)

    next_gate = (
        "Approved by human operator; release may proceed."
        if merged
        else "Approved by human operator; merge stub skipped because no PR URL was recorded."
    )
    store.update_run(
        run_id,
        status=FactoryRunStatus.released,
        next_gate=next_gate,
        outcome="approved_by_human",
    )
    return store.get_run_detail(run_id)


@router.post("/runs/{run_id}/reject", response_model=FactoryRunDetail)
def reject_run(run_id: str) -> FactoryRunDetail:
    try:
        store.get_run(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Factory run not found") from exc

    from app.factory import portal

    portal.escalate(
        type("ChangeRequestStub", (), {"run_id": run_id, "title": "Human rejection"})(),
        "Human operator rejected the generated patch.",
    )
    store.update_run(
        run_id,
        status=FactoryRunStatus.escalated,
        next_gate="Rejected by human operator; escalation required.",
        outcome="rejected_by_human",
    )
    return store.get_run_detail(run_id)


@router.post("/audit/start")
def start_audit_scheduler() -> dict[str, object]:
    return scheduler.start()


@router.post("/audit/stop")
async def stop_audit_scheduler() -> dict[str, object]:
    return await scheduler.stop()


@router.get("/audit/status")
def audit_scheduler_status() -> dict[str, object]:
    return scheduler.status()

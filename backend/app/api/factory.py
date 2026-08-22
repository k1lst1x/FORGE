from fastapi import APIRouter, HTTPException

from app.factory import engine, scheduler, store
from app.factory.models import FactoryRun, FactoryRunCreate, FactoryRunDetail, Finding

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


@router.post("/audit/start")
def start_audit_scheduler() -> dict[str, object]:
    return scheduler.start()


@router.post("/audit/stop")
async def stop_audit_scheduler() -> dict[str, object]:
    return await scheduler.stop()


@router.get("/audit/status")
def audit_scheduler_status() -> dict[str, object]:
    return scheduler.status()

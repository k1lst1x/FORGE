from enum import StrEnum
from uuid import uuid4

from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(prefix="/factory", tags=["factory"])


class FactoryRunStatus(StrEnum):
    awaiting_human = "awaiting_human"
    planned = "planned"
    building = "building"
    auditing = "auditing"
    patching = "patching"
    verified = "verified"


class FactoryRunCreate(BaseModel):
    brief: str = Field(min_length=1, max_length=4000)
    trigger: str = Field(default="manual", min_length=1, max_length=80)


class FactoryRun(BaseModel):
    id: str
    brief: str
    trigger: str
    status: FactoryRunStatus
    next_gate: str


_runs: list[FactoryRun] = [
    FactoryRun(
        id="demo-build-audit-loop",
        brief=(
            "Build a feature, audit it every five minutes, patch verified issues, "
            "wait for approval."
        ),
        trigger="seed",
        status=FactoryRunStatus.awaiting_human,
        next_gate="Human approval before applying the generated patch.",
    )
]


@router.get("/runs", response_model=list[FactoryRun])
def list_runs() -> list[FactoryRun]:
    return _runs


@router.post("/runs", response_model=FactoryRun, status_code=201)
def create_run(payload: FactoryRunCreate) -> FactoryRun:
    run = FactoryRun(
        id=str(uuid4()),
        brief=payload.brief,
        trigger=payload.trigger,
        status=FactoryRunStatus.planned,
        next_gate="Connect Port workflow and SigNoz trace before execution.",
    )
    _runs.insert(0, run)
    return run

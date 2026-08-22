from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class IntakeType(StrEnum):
    brief = "brief"
    finding = "finding"


class FactoryRunStatus(StrEnum):
    planned = "planned"
    gathering_context = "gathering_context"
    triaging = "triaging"
    acting = "acting"
    verifying = "verifying"
    awaiting_human = "awaiting_human"
    released = "released"
    escalated = "escalated"
    failed = "failed"


class FactoryStepStatus(StrEnum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    skipped = "skipped"


class FindingSeverity(StrEnum):
    low = "LOW"
    medium = "MED"
    high = "HIGH"


class TriageClassification(StrEnum):
    autofix_safe = "AUTOFIX_SAFE"
    needs_human_design = "NEEDS_HUMAN_DESIGN"
    false_positive = "FALSE_POSITIVE"
    upstream_outage = "UPSTREAM_OUTAGE"
    duplicate = "DUPLICATE"


class ChangeRequest(BaseModel):
    run_id: str
    intake: IntakeType
    title: str
    brief_text: str | None = None
    finding: dict[str, Any] | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    classification: TriageClassification | None = None
    should_act: bool = True
    justification: str | None = None
    changeset: list[dict[str, Any]] = Field(default_factory=list)
    verify: dict[str, Any] = Field(default_factory=dict)
    branch: str | None = None
    pr_url: str | None = None
    attempts: int = 0
    trace_id: str | None = None
    outcome: str | None = None


class FactoryRunCreate(BaseModel):
    brief: str = Field(min_length=1, max_length=4000)
    trigger: str = Field(default="manual", min_length=1, max_length=80)
    auto_start: bool = True


class FactoryRun(BaseModel):
    id: str
    intake: IntakeType
    title: str
    brief: str | None = None
    trigger: str
    status: FactoryRunStatus
    next_gate: str | None = None
    branch: str | None = None
    pr_url: str | None = None
    trace_id: str | None = None
    outcome: str | None = None
    created_at: str
    updated_at: str


class FactoryStep(BaseModel):
    id: int
    run_id: str
    name: str
    status: FactoryStepStatus
    summary: str | None = None
    started_at: str | None = None
    completed_at: str | None = None


class Finding(BaseModel):
    id: str
    run_id: str
    check_id: str
    severity: FindingSeverity
    route: str
    title: str
    evidence: str
    suggested_fix_hint: str | None = None
    occurrences: int = 1
    created_at: str


class FactoryRunDetail(FactoryRun):
    steps: list[FactoryStep] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)

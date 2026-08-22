"""
forge/models.py — the shared vocabulary.

This is the file the whole team agreed on in the stub session (§08). The
ChangeRequest below is THE object every stage passes around, and the frozen
field list from the plan is reproduced verbatim at the top of the dataclass.

Fields added after the freeze are grouped at the bottom, all with defaults, so
nothing anyone else wrote breaks. If you need something new, add a field there
or stash it in `context` — do not change a field that already exists.

Owner: Rohit.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field

# --- intake types ------------------------------------------------------------
INTAKE_BRIEF = "brief"
INTAKE_FINDING = "finding"

# --- the eight steps, plus AUDIT which closes every run ----------------------
STAGES = (
    "INTAKE",
    "CONTEXT",
    "TRIAGE",
    "PLAN",
    "ACT",
    "VERIFY",
    "GATE",
    "RELEASE",
    "AUDIT",
)

# --- triage classifications (§04) --------------------------------------------
AUTOFIX_SAFE = "AUTOFIX_SAFE"
NEEDS_HUMAN_DESIGN = "NEEDS_HUMAN_DESIGN"
FALSE_POSITIVE = "FALSE_POSITIVE"
UPSTREAM_OUTAGE = "UPSTREAM_OUTAGE"
DUPLICATE = "DUPLICATE"
NEW_FEATURE = "NEW_FEATURE"  # the brief path's classification (Loop A)

#: classifications that mean "do not write code"
DECLINING = frozenset({NEEDS_HUMAN_DESIGN, FALSE_POSITIVE, UPSTREAM_OUTAGE, DUPLICATE})

# --- run outcomes ------------------------------------------------------------
OUTCOME_MERGED = "merged"
OUTCOME_REJECTED = "rejected_by_human"
OUTCOME_ESCALATED = "escalated"
OUTCOME_SUPPRESSED = "suppressed"
OUTCOME_DUPLICATE = "attached_to_existing_run"
OUTCOME_BACKED_OFF = "backed_off"
OUTCOME_VERIFY_FAILED = "verify_failed_escalated"
OUTCOME_MERGE_FAILED = "merge_failed"
OUTCOME_ERROR = "error"

# --- scorecard grades (§03) --------------------------------------------------
GOLD, SILVER, BRONZE = "gold", "silver", "bronze"
GRADE_VALUE = {GOLD: 3, SILVER: 2, BRONZE: 1}  # what forge_security_grade exports


def new_run_id() -> str:
    return f"run_{uuid.uuid4().hex[:8]}"


def new_finding_id() -> str:
    return f"f_{uuid.uuid4().hex[:4]}"


def grade_for(findings: list[dict]) -> str:
    """Gold = 0 HIGH and 0 MED. Silver = 0 HIGH. Bronze = 1+ HIGH."""
    sev = {(f.get("severity") or "").upper() for f in findings}
    if "HIGH" in sev:
        return BRONZE
    if "MED" in sev:
        return SILVER
    return GOLD


@dataclass
class ChangeRequest:
    """One unit of work. A brief and a finding both become one of these."""

    # ---- frozen in the stub session, do not reorder or rename ----
    run_id: str
    intake: str  # "brief" | "finding"
    title: str
    brief_text: str | None = None
    finding: dict | None = None
    context: dict = field(default_factory=dict)
    classification: str | None = None  # set by TRIAGE
    should_act: bool = True  # TRIAGE can set False
    justification: str | None = None  # why, if should_act is False
    changeset: list = field(default_factory=list)  # [{path, content, reason}]
    verify: dict = field(default_factory=dict)
    branch: str | None = None
    pr_url: str | None = None
    attempts: int = 0
    trace_id: str | None = None
    outcome: str | None = None

    # ---- added after the freeze, all defaulted, safe for everyone ----
    stage: str = "INTAKE"  # current step, for Port's factory_run.stage
    status: str = "queued"  # queued | running | escalated | rejected | done | failed
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    approval_id: str | None = None
    approved: bool | None = None
    #: ONE ENTRY PER VERIFY ATTEMPT, appended and never overwritten.
    #: `verify` above holds only the LAST result, which is why a run that was
    #: rejected three times used to reach a human with no record of what the
    #: three rejections said -- an hour of reading logs to learn something the
    #: run already knew. This is what summary() publishes as "verify", and
    #: therefore what GET /api/runs/{id} serves.
    verify_attempts: list = field(default_factory=list)

    # ---------------- convenience ----------------
    @property
    def duration_ms(self) -> float:
        end = self.finished_at if self.finished_at is not None else time.time()
        return round((end - self.created_at) * 1000, 2)

    @property
    def route(self) -> str | None:
        """The route this run is about, if it is about one."""
        if self.finding:
            return self.finding.get("route")
        return self.context.get("route")

    @property
    def check_id(self) -> str | None:
        return (self.finding or {}).get("check_id")

    @property
    def files_changed(self) -> list[str]:
        return [c["path"] for c in self.changeset]

    def as_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> dict:
        """The small, flat shape worth logging or putting on a span."""
        return {
            "run_id": self.run_id,
            "intake": self.intake,
            "title": self.title,
            "stage": self.stage,
            "status": self.status,
            "classification": self.classification,
            "should_act": self.should_act,
            "justification": self.justification,
            "route": self.route,
            "check_id": self.check_id,
            "attempts": self.attempts,
            "files_changed": self.files_changed,
            "branch": self.branch,
            "pr_url": self.pr_url,
            "approved": self.approved,
            # The whole verification history, not just the last attempt.
            "verify": list(self.verify_attempts),
            "outcome": self.outcome,
            "trace_id": self.trace_id,
            "duration_ms": self.duration_ms,
        }


class ChangeSet(list):
    """A list of {path, content, reason} that also carries how it was produced.

    It subclasses list on purpose: every existing call site treats a changeset
    as a list of dicts and keeps working unchanged, while PLAN can still hang
    the rationale and token counts off it for the span. Whole file contents,
    never diffs -- diffs are fragile to generate, whole files are reliable.
    """

    def __init__(self, files=(), **meta):
        super().__init__(files)
        self.rationale: str = meta.get("rationale", "")
        self.model: str | None = meta.get("model")
        self.tokens_in: int = meta.get("tokens_in", 0)
        self.tokens_out: int = meta.get("tokens_out", 0)
        self.attempt: int = meta.get("attempt", 1)
        self.test_included: bool = meta.get("test_included", False)
        self.rejected_paths: list = meta.get("rejected_paths", [])

    @property
    def paths(self) -> list[str]:
        return [f["path"] for f in self]


@dataclass
class TriageResult:
    """What triage.classify returns. Rohit, Block 3."""

    classification: str
    should_act: bool
    justification: str
    confidence: float = 0.0
    blast_radius: str = "unknown"  # contained | service | clients | unknown
    # ---- added after the freeze, all defaulted ----
    #: guard | model | heuristic | fallback -- which path actually decided.
    #: On the span, so "which of these did the model decide?" has a straight answer.
    decided_by: str = "model"
    model: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0


@dataclass
class VerifyResult:
    """Two independent verifications. Rohit, Block 5."""

    ok: bool
    tests_passed: bool
    audit_before: dict = field(default_factory=dict)
    audit_after: dict = field(default_factory=dict)
    findings_closed: list = field(default_factory=list)
    findings_introduced: list = field(default_factory=list)
    evidence: str = ""

    # ---- added after the freeze, all defaulted ----
    #: Raw pytest output. `evidence` is written for a human reading a pull
    #: request; this is what the planner is shown verbatim on a retry.
    tests_output: str = ""
    #: One sentence naming why this attempt was rejected, so the run record and
    #: the console do not make anyone read the whole evidence blob to find out.
    rejected_reason: str = ""
    #: Findings this run was supposed to close that the fresh audit still saw.
    #: The target finding, plus every open sibling in its family.
    findings_still_open: list = field(default_factory=list)
    #: Which attempt produced this result. 1-based, matching cr.attempts + 1.
    attempt: int = 1

    def as_dict(self) -> dict:
        return asdict(self)

    def record(self) -> dict:
        """The compact per-attempt row that lands on the run and in the API."""
        return {
            "attempt": self.attempt,
            "ok": self.ok,
            "tests_passed": self.tests_passed,
            # Trimmed: the run record is read over HTTP and lives in runs.json.
            # The full output stays on the VerifyResult and in the evidence.
            "tests_output": (self.tests_output or "")[-4000:],
            "findings_closed": list(self.findings_closed),
            "findings_introduced": list(self.findings_introduced),
            "findings_still_open": list(self.findings_still_open),
            "rejected_reason": self.rejected_reason,
        }


@dataclass
class AuditResult:
    """What audit.run_audit returns. Rohit, Block 2 fills this in for real."""

    base_url: str = ""
    routes_checked: list = field(default_factory=list)
    findings: list = field(default_factory=list)
    grades: dict = field(default_factory=dict)  # route -> grade
    duration_ms: float = 0.0
    reachable: bool = True  # False when the target served nothing at all
    #: route -> served HTML. Stays in process: findings go to Port, bodies do not.
    pages: dict = field(default_factory=dict)

    @property
    def findings_high(self) -> list:
        return [f for f in self.findings if (f.get("severity") or "").upper() == "HIGH"]

    @property
    def worst_grade(self) -> str:
        if not self.grades:
            return GOLD
        return min(self.grades.values(), key=lambda g: GRADE_VALUE.get(g, 0))

    def for_route(self, route: str) -> list:
        return [f for f in self.findings if f.get("route") == route]

    def as_dict(self) -> dict:
        return {
            "base_url": self.base_url,
            "routes_checked": self.routes_checked,
            "findings_total": len(self.findings),
            "findings_high": len(self.findings_high),
            "grades": self.grades,
            "worst_grade": self.worst_grade,
            "duration_ms": self.duration_ms,
            "reachable": self.reachable,
        }

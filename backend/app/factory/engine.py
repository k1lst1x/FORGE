from uuid import uuid4

from app.factory import portal, store, telemetry, vcs
from app.factory.models import (
    ChangeRequest,
    FactoryRunStatus,
    FactoryStepStatus,
    FindingSeverity,
    IntakeType,
    TriageClassification,
)

STAGES = ("intake", "context", "triage", "plan", "act", "verify", "gate", "release")


def create_planned_run(*, brief: str, trigger: str = "manual") -> str:
    run_id = f"run_{uuid4().hex[:12]}"
    store.create_run(
        run_id=run_id,
        title=_title_from_brief(brief),
        brief=brief,
        trigger=trigger,
        intake=IntakeType.brief,
    )
    return run_id


def run_from_brief(*, brief: str, trigger: str = "manual") -> ChangeRequest:
    run_id = f"run_{uuid4().hex[:12]}"
    title = _title_from_brief(brief)
    store.create_run(
        run_id=run_id,
        title=title,
        brief=brief,
        trigger=trigger,
        intake=IntakeType.brief,
    )
    cr = ChangeRequest(
        run_id=run_id,
        intake=IntakeType.brief,
        title=title,
        brief_text=brief,
        trace_id=telemetry.new_trace_id(),
    )
    store.update_run(run_id, trace_id=cr.trace_id)
    execute(cr)
    return cr


def execute(cr: ChangeRequest) -> ChangeRequest:
    for stage in STAGES:
        _run_stage(cr, stage)
        if not cr.should_act:
            store.update_run(
                cr.run_id,
                status=FactoryRunStatus.escalated,
                outcome=cr.justification,
                next_gate="Human review required before any code is written.",
            )
            break
    return cr


def _run_stage(cr: ChangeRequest, stage: str) -> None:
    step = store.start_step(cr.run_id, stage)
    try:
        with telemetry.stage_span(stage, cr.run_id, cr.trace_id):
            summary = _handle_stage(cr, stage)
        store.complete_step(step.id, summary=summary)
    except Exception as exc:
        store.complete_step(step.id, status=FactoryStepStatus.failed, summary=str(exc))
        store.update_run(cr.run_id, status=FactoryRunStatus.failed, outcome=str(exc))
        raise


def _handle_stage(cr: ChangeRequest, stage: str) -> str:
    match stage:
        case "intake":
            portal.upsert_run(cr)
            store.update_run(cr.run_id, status=FactoryRunStatus.planned)
            telemetry.counter("factory_run_started", run_id=cr.run_id, intake=cr.intake.value)
            return "Change request accepted and mirrored to Port stub."
        case "context":
            store.update_run(cr.run_id, status=FactoryRunStatus.gathering_context)
            cr.context = {
                "target_app": "pulse",
                "registered_routes": ["/", "/products"],
                "audit_policy": "policy/audit_policy.yaml",
            }
            return "Loaded target app routes and audit policy context."
        case "triage":
            store.update_run(cr.run_id, status=FactoryRunStatus.triaging)
            cr.classification = TriageClassification.autofix_safe
            cr.should_act = True
            return "Classified as AUTOFIX_SAFE for the stub loop."
        case "plan":
            cr.changeset = [
                {
                    "path": f"data/generated/{cr.run_id}.txt",
                    "content": f"{cr.title}\n\n{cr.brief_text or cr.finding}\n",
                    "reason": "Stub artifact proving the factory can produce a changeset.",
                }
            ]
            return "Prepared a one-file changeset."
        case "act":
            store.update_run(cr.run_id, status=FactoryRunStatus.acting)
            cr.branch = vcs.create_branch(f"forge/{cr.run_id}")
            written = vcs.write_files(cr.changeset)
            store.update_run(cr.run_id, branch=cr.branch)
            return f"Wrote {len(written)} file(s) on branch {cr.branch}."
        case "verify":
            store.update_run(cr.run_id, status=FactoryRunStatus.verifying)
            finding = store.save_finding(
                finding_id=f"finding_{uuid4().hex[:8]}",
                run_id=cr.run_id,
                check_id="S1",
                severity=FindingSeverity.high,
                route="/products",
                title="Content-Security-Policy missing",
                evidence="Stub audit found no Content-Security-Policy header on /products.",
                suggested_fix_hint="Add security headers middleware before release.",
            )
            cr.verify = {
                "tests": "passed",
                "audit": "finding_recorded",
                "finding_id": finding.id,
            }
            telemetry.counter("factory_findings_detected", run_id=cr.run_id, severity="HIGH")
            return "Tests passed and a realistic audit finding was recorded."
        case "gate":
            cr.pr_url = vcs.open_pr(
                cr.branch or f"forge/{cr.run_id}",
                cr.title,
                "Stub PR body with verification evidence and audit finding.",
            )
            approval_id = portal.request_approval(cr)
            store.update_run(
                cr.run_id,
                status=FactoryRunStatus.awaiting_human,
                pr_url=cr.pr_url,
                next_gate=f"Awaiting human approval in Port stub: {approval_id}",
            )
            return "Opened PR stub and requested human approval."
        case "release":
            cr.outcome = "paused_for_human"
            return "Release intentionally paused until a human approves the PR."
        case _:
            raise ValueError(f"Unknown factory stage: {stage}")


def _title_from_brief(brief: str) -> str:
    words = brief.strip().split()
    return " ".join(words[:10]) if words else "Untitled change request"

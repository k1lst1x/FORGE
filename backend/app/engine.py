"""
forge/engine.py -- FORGE's eight-step state machine.

ONE code path. TWO intake types.

    INTAKE -> CONTEXT -> TRIAGE -> PLAN -> ACT -> VERIFY -> GATE -> RELEASE
    ...and AUDIT, which closes every run and writes the record.

A brief (Loop A) and a finding (Loop B) both become a ChangeRequest at INTAKE.
After that the engine does not branch on intake type, with exactly two
exceptions the build guide allows:

  * CONTEXT gathers different evidence for a brief than for a finding
  * PLAN calls planner.plan_feature instead of planner.plan_fix

Everything else -- triage, act, verify, the human gate, release, audit -- is
the same code, the same spans, and the same approval gate for both loops. That
is the claim we make to a judge, so it has to be literally true in this file.

TRIAGE may set should_act = False. That is the headline feature, not an edge
case: the run skips PLAN/ACT/VERIFY/GATE entirely, escalates with a written
justification a human will read, and still closes with AUDIT.

VERIFY failure loops back to PLAN with the failure output added to context.
Two retries, then escalate.

OWNER: ROHIT. Damir owns vcs / portal / brightdata / store / telemetry -- this
module only ever calls their frozen signatures, never their internals.
"""
from __future__ import annotations

import functools
import logging
import re
import threading
import time
import traceback
import uuid
from pathlib import Path

from app import audit as audit_mod
from app import brightdata, config, llm, planner, portal, store, telemetry
from app import triage as triage_mod
from app import vcs
from app import verify as verify_mod
from app.models import (
    DUPLICATE,
    FALSE_POSITIVE,
    INTAKE_BRIEF,
    INTAKE_FINDING,
    OUTCOME_BACKED_OFF,
    OUTCOME_DUPLICATE,
    OUTCOME_ERROR,
    OUTCOME_ESCALATED,
    OUTCOME_MERGED,
    OUTCOME_MERGE_FAILED,
    OUTCOME_REJECTED,
    OUTCOME_SUPPRESSED,
    OUTCOME_VERIFY_FAILED,
    UPSTREAM_OUTAGE,
    ChangeRequest,
    new_run_id,
)

log = logging.getLogger("forge.engine")

#: 1 attempt plus 2 retries, then escalate
MAX_PLAN_ATTEMPTS = config.MAX_PLAN_ATTEMPTS


# --------------------------------------------------------------------------
# plumbing
# --------------------------------------------------------------------------
def _safe(fn, *args, **kwargs):
    """Call something Damir owns. A control-plane wobble must not kill a run.

    Port being slow, or SigNoz rejecting a metric, is not a reason to abandon a
    security fix half way through. Real work -- planning, writing, verifying,
    merging -- is deliberately NOT wrapped: those failures are real failures.
    """
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        log.warning("non-fatal %s failed: %s", getattr(fn, "__name__", "?"), exc)
        return None


def _upsert(cr: ChangeRequest) -> None:
    """Push the run to Port on entering and leaving every step, so the run
    animates live in the Port UI while John is recording."""
    _safe(portal.upsert_run, cr)


def _tag(span, cr: ChangeRequest) -> None:
    if span is None:
        return
    attrs = {
        "forge.run_id": cr.run_id,
        "forge.stage": cr.stage,
        "forge.intake": cr.intake,
        "forge.title": cr.title,
        "forge.attempt": cr.attempts + 1,
    }
    if cr.classification:
        attrs["forge.classification"] = cr.classification
    if cr.route:
        attrs["forge.route"] = cr.route
    if cr.check_id:
        attrs["forge.check_id"] = cr.check_id
    for key, value in attrs.items():
        try:
            span.set_attribute(key, value)
        except Exception:
            pass


def _run_with_timeout(fn, *args, timeout_seconds: float | None = None, **kwargs):
    """Protect a stage from blocking forever; fail the run without hanging the control loop."""
    timeout_seconds = float(timeout_seconds if timeout_seconds is not None else config.FORGE_RUN_TIMEOUT_SECONDS)
    if timeout_seconds <= 0:
        return fn(*args, **kwargs)

    result = {}
    error = {}

    def worker():
        try:
            result["value"] = fn(*args, **kwargs)
        except BaseException as exc:  # pragma: no cover - forwarded while the run is marked failed
            error["exc"] = exc

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(timeout=timeout_seconds)

    if thread.is_alive():
        raise TimeoutError(f"run exceeded wall-clock timeout of {timeout_seconds}s")
    if "exc" in error:
        raise error["exc"]
    return result.get("value")


def _stage(name: str, span_name: str | None = None):
    """Wrap a step: its own span, its own Port update, its own duration metric.

    Every step gets identical treatment, which is what makes the trace read as
    one shape no matter which loop opened the run.
    """

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(cr: ChangeRequest, *args, **kwargs) -> ChangeRequest:
            cr.stage = name
            started = time.perf_counter()
            with telemetry.stage_span(span_name or ("forge." + name.lower()), cr.run_id) as span:
                _tag(span, cr)
                _upsert(cr)
                try:
                    result = _run_with_timeout(fn, cr, span, *args, timeout_seconds=config.FORGE_RUN_TIMEOUT_SECONDS, **kwargs)
                    cr = result if isinstance(result, ChangeRequest) else cr
                except Exception as exc:
                    cr.outcome = OUTCOME_ERROR
                    cr.status = "failed"
                    cr.finished_at = time.time()
                    if span is not None:
                        try:
                            span.record_exception(exc)
                        except Exception:
                            pass
                    _upsert(cr)
                    raise
                took = (time.perf_counter() - started) * 1000
                _tag(span, cr)
                _safe(telemetry.histogram, "forge_stage_duration_ms", took, stage=name, intake=cr.intake)
                _upsert(cr)
                return cr

        return wrapper

    return decorator


def _read_text(path: Path, limit: int = 20000) -> str | None:
    try:
        return path.read_text(encoding="utf-8")[:limit]
    except Exception:
        return None


def _repo_path(path: Path) -> str:
    """A repo-relative path with forward slashes, matching what the model returns.

    The planner normalises every path it is given back to this shape, and its
    rails key off it -- including the one that refuses a rewrite which deletes
    most of a file. Hand it a Windows Path rendered with a backslash and that lookup
    misses, the rail sees no original to compare against, and a patch that
    replaced the whole app with a stub sails through.
    """
    try:
        resolved = path.resolve().relative_to(Path(config.REPO_ROOT).resolve())
    except Exception:
        resolved = path
    return str(resolved).replace("\\", "/").lstrip("./")


def _serves_route(body: str, route: str) -> bool:
    """Does this file actually declare a handler for that route?

    Substring matching does NOT work here and quietly broke the whole fix loop:
    the route is "/", which appears in every import path, every URL and every
    comment, so the first file in the glob won -- pulse/routes/security.py. The
    planner was then shown a route module and asked to fix a response header on
    "/", which is a question that file cannot answer. That is run_f1e86721, and
    it is why "the planner writes patches that cannot close the finding" was the
    symptom rather than the cause.
    """
    pattern = re.compile(
        r"@(?:app|router)\.(?:get|post|put|delete|patch|head|options)\(\s*[\"']"
        + re.escape(route)
        + r"[\"']"
    )
    return bool(pattern.search(body or ""))


def _route_file(route: str | None) -> Path | None:
    """The file that actually serves a route, by its decorator -- not by substring.

    pulse/main.py is checked FIRST because it defines the app, and because an
    app-wide concern attributed to "/" belongs to it whatever else matches.
    """
    if not route:
        return None
    pulse = Path(config.PULSE_DIR)
    main = pulse / "main.py"
    candidates = [main] + sorted((pulse / "routes").glob("*.py")) if (pulse / "routes").is_dir() else [main]
    for candidate in candidates:
        body = _read_text(candidate)
        if body and _serves_route(body, route):
            return candidate
    return main if main.exists() else None


def _context_files(finding: dict) -> dict:
    """The files the planner needs, keyed the way the planner keys them.

    Always includes pulse/main.py. Response headers, middleware, CORS, docs
    guards and exception handlers all live there regardless of which route the
    finding is attributed to, and a model asked to return the FULL content of a
    file it was never shown will invent one -- which is how a patch for a header
    on "/" came back as a 23-line main.py with every other route deleted.
    """
    files: dict = {}
    source = _route_file((finding or {}).get("route"))
    if source is not None and source.exists():
        files[_repo_path(source)] = _read_text(source) or ""
    main = Path(config.PULSE_DIR) / "main.py"
    if main.exists():
        files.setdefault(_repo_path(main), _read_text(main) or "")
    return files


def _page_source(finding: dict) -> str:
    """The page as it was actually served.

    An empty string here is meaningful, not missing data: it is how the engine
    tells a broken app apart from an absent one. Triage needs that distinction
    to classify UPSTREAM_OUTAGE rather than opening seventeen fix requests
    against an app that is simply down.
    """
    if finding.get("reachable") is False:
        return ""
    if "page_source" in finding:
        return finding.get("page_source") or ""
    route = finding.get("route") or "/"
    fetched = _safe(brightdata.scrape_markdown, config.PULSE_BASE_URL.rstrip("/") + route)
    return fetched or ""


def _prior_attempts(cr: ChangeRequest) -> list[dict]:
    """Runs that already tried to fix this same check on this same route."""
    finding = cr.finding or {}
    rows = _safe(store.list_runs, 50) or []
    history = []
    for row in rows:
        if row.get("run_id") == cr.run_id:
            continue
        if row.get("check_id") == finding.get("check_id") and row.get("route") == finding.get("route"):
            history.append({
                "run_id": row.get("run_id"),
                "check_id": row.get("check_id"),
                "route": row.get("route"),
                "status": "in_flight" if row.get("status") in ("running", "awaiting_approval") else "closed",
                "outcome": row.get("outcome"),
                "classification": row.get("classification"),
            })
    return history


def _family_context(finding: dict) -> tuple[str | None, list[dict]]:
    """Every open finding that shares this one's fix, on this one's route.

    S1-S6 are six findings and ONE security-headers middleware. Planned one at a
    time the run cannot converge: close S1 and the fresh audit still reports
    S2-S6 on that route, so VERIFY rejects, and the retry meets the same five.
    The whole set goes to the planner together and VERIFY expects the whole set
    closed -- see the `family` key in policy/audit_policy.yaml.

    Returns (None, []) for a finding that stands alone, which is most of them.
    """
    finding = finding or {}
    route, check_id = finding.get("route"), finding.get("check_id")
    family = finding.get("family") or _safe(audit_mod.family_of, check_id)
    if not family:
        return None, []

    members = set(_safe(audit_mod.family_members, family) or [])
    siblings: dict = {}
    for row in _safe(store.open_findings, route) or []:
        if row.get("check_id") in members and row.get("route") == route:
            siblings[row.get("finding_id")] = row
    # The finding that opened this run is always in the set, whether or not the
    # catalog has caught up with it yet.
    siblings[finding.get("finding_id")] = finding
    ordered = sorted(siblings.values(), key=lambda f: str(f.get("check_id")))
    log.info("finding %s belongs to family %s -- %s open on %s",
             check_id, family, len(ordered), route)
    return family, ordered


def _affected_routes(cr: ChangeRequest) -> list[str]:
    """Which routes the closing audit checks.

    "/" is ALWAYS included, and that is not cosmetic. Findings are identified by
    a hash of check_id + route, and the app-level checks (exposed paths, stack
    traces, docs) attribute to the first route audited. Leave "/" out and the
    same defect gets one id from the scheduled audit and a different one here,
    which quietly breaks dedupe and the occurrence count.

    A brief's created routes come from the changeset the factory just wrote --
    NOT from context["existing_routes"], which holds file paths.
    """
    routes: list[str] = []
    if cr.route:
        routes.append(cr.route)
    routes.extend(cr.context.get("created_routes") or [])
    if "/" not in routes:
        routes.append("/")
    seen, ordered = set(), []
    for route in routes:
        if route not in seen:
            seen.add(route)
            ordered.append(route)
    return ordered


def _pr_body(cr: ChangeRequest) -> str:
    """A human reads this. What changed, why, the evidence, the verify result."""
    lines = ["## What changed", ""]
    for change in cr.changeset:
        lines.append("- " + change["path"] + " -- " + change.get("reason", ""))
    lines += ["", "## Why", "", cr.justification or "(no justification recorded)", ""]
    if cr.finding:
        lines += [
            "## The finding",
            "",
            "- check: " + str(cr.check_id) + " (" + str(cr.finding.get("severity")) + ")",
            "- route: " + str(cr.route),
            "- evidence: " + str(cr.finding.get("evidence")),
            "",
        ]
    created = cr.context.get("created_routes") or []
    if created:
        lines += [
            "## What happens to this page next",
            "",
            "The scheduled audit picks up "
            + ", ".join("`" + r + "`" for r in created)
            + " on its next pass, and grades it against the same seventeen checks as every other "
            "page this factory has shipped. The factory does not stop caring once this merges.",
            "",
        ]
    lines += [
        "## Verification -- two independent checks",
        "",
        "- tests passed: " + str(cr.verify.get("tests_passed")),
        "- findings closed: " + str(cr.verify.get("findings_closed")),
        "- findings introduced: " + str(cr.verify.get("findings_introduced")),
        "- evidence: " + str(cr.verify.get("evidence")),
        "",
    ]
    if len(cr.verify_attempts) > 1:
        lines += ["### Earlier attempts", ""]
        for entry in cr.verify_attempts[:-1]:
            lines.append(
                "- attempt " + str(entry.get("attempt")) + ": rejected -- "
                + str(entry.get("rejected_reason") or "see the run record")
            )
        lines.append("")
    lines += [
        "## Run",
        "",
        "- run_id: " + cr.run_id,
        "- intake: " + cr.intake,
        "- triage: " + str(cr.classification),
        "- attempts: " + str(cr.attempts + 1),
        "- trace_id: " + str(cr.trace_id),
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# the eight steps
# --------------------------------------------------------------------------
@_stage("INTAKE")
def intake(cr: ChangeRequest, span) -> ChangeRequest:
    """A brief and a finding arrive at the same front door and leave identical."""
    cr.trace_id = cr.trace_id or _safe(telemetry.current_trace_id) or uuid.uuid4().hex
    cr.status = "running"
    if span is not None:
        span.set_attribute("forge.trace_id", cr.trace_id)
    _safe(telemetry.counter, "forge_runs_total", 1, intake=cr.intake)
    log.info("INTAKE %s intake=%s title=%s", cr.run_id, cr.intake, cr.title)
    return cr


@_stage("CONTEXT")
def context(cr: ChangeRequest, span) -> ChangeRequest:
    """Assemble the evidence. One of the two places the guide allows a branch."""
    cr.context["policy_path"] = config.POLICY_PATH
    cr.context["policy"] = _read_text(Path(config.POLICY_PATH)) or ""

    if cr.intake == INTAKE_FINDING:
        finding = cr.finding or {}
        route = finding.get("route")
        source_file = _route_file(route)
        cr.context["route"] = route
        cr.context["page_source"] = _page_source(finding)
        cr.context["source_path"] = _repo_path(source_file) if source_file else None
        cr.context["file_contents"] = _context_files(finding)
        family, family_findings = _family_context(finding)
        cr.context["family"] = family
        cr.context["family_findings"] = family_findings
        # History means PRIOR FIX ATTEMPTS, not currently-open findings.
        # Passing open findings put the finding under triage into its own
        # history, and the model correctly read that as "already in flight" and
        # returned DUPLICATE for everything -- so nothing was ever fixed.
        cr.context["history"] = _prior_attempts(cr)
    else:
        pulse = Path(config.PULSE_DIR)
        routes_dir, templates_dir = pulse / "routes", pulse / "templates"
        route_files = sorted(routes_dir.glob("*.py")) if routes_dir.is_dir() else []
        template_files = sorted(templates_dir.glob("*.html")) if templates_dir.is_dir() else []
        cr.context["existing_routes"] = [_repo_path(p) for p in route_files]
        cr.context["templates"] = {p.name: _read_text(p) or "" for p in template_files}
        cr.context["file_contents"] = {_repo_path(p): _read_text(p) or "" for p in route_files}
        cr.context["page_source"] = ""
        cr.context["history"] = []

    if span is not None:
        span.set_attribute("context.files", len(cr.context.get("file_contents") or {}))
        span.set_attribute("context.history", len(cr.context.get("history") or []))
        span.set_attribute("context.policy_loaded", bool(cr.context.get("policy")))
        span.set_attribute("context.family", cr.context.get("family") or "none")
        span.set_attribute("context.family_findings", len(cr.context.get("family_findings") or []))
    return cr


@_stage("TRIAGE")
def triage(cr: ChangeRequest, span) -> ChangeRequest:
    """Decide whether to act at all.

    A brief is handed to classify() wearing the same shape as a finding, so
    there is exactly one triage call site serving both loops.
    """
    subject = cr.finding
    if subject is None:
        subject = {
            "check_id": "BRIEF",
            "severity": "NONE",
            "route": None,
            "title": cr.title,
            "evidence": cr.brief_text or "",
        }

    result = triage_mod.classify(
        subject,
        cr.context.get("page_source", ""),
        cr.context.get("file_contents", {}),
        cr.context.get("history", []),
    )

    cr.classification = result.classification
    cr.should_act = bool(result.should_act)
    cr.justification = result.justification

    if span is not None:
        span.set_attribute("triage.classification", result.classification)
        span.set_attribute("triage.should_act", cr.should_act)
        span.set_attribute("triage.justification", result.justification)
        span.set_attribute("triage.confidence", result.confidence)
        span.set_attribute("triage.blast_radius", result.blast_radius)
        span.set_attribute("triage.decided_by", result.decided_by)
        span.set_attribute("triage.model", result.model or "none")
        span.set_attribute("triage.tokens_in", result.tokens_in)
        span.set_attribute("triage.tokens_out", result.tokens_out)
    cr.context["triage"] = {
        "decided_by": result.decided_by,
        "confidence": result.confidence,
        "blast_radius": result.blast_radius,
        "model": result.model,
    }
    _safe(telemetry.counter, "forge_triage_total", 1, classification=result.classification, decided_by=result.decided_by)

    if not cr.should_act and span is not None:
        span.add_event(
            "forge.decision.declined",
            {
                "classification": result.classification,
                "justification": result.justification,
                "confidence": result.confidence,
            },
        )
    log.info("TRIAGE %s -> %s (act=%s)", cr.run_id, cr.classification, cr.should_act)
    return cr


@_stage("PLAN")
def plan(cr: ChangeRequest, span) -> ChangeRequest:
    """Produce a ChangeSet. The other place the guide allows a branch."""
    # On a retry these carry the previous attempt and the exact verify failure,
    # so the second call has strictly more information than the first.
    shared = {"previous": cr.context.get("previous_attempt"), "attempt": cr.attempts + 1}

    if cr.intake == INTAKE_FINDING:
        family = cr.context.get("family")
        cr.changeset = planner.plan_fix(
            cr.finding,
            {"classification": cr.classification, "justification": cr.justification},
            cr.context.get("file_contents", {}),
            cr.context.get("policy", ""),
            family={"name": family, "findings": cr.context.get("family_findings") or []} if family else None,
            **shared,
        )
    else:
        cr.changeset = planner.plan_feature(
            cr.brief_text,
            cr.context.get("file_contents", {}),
            cr.context.get("templates", {}),
            cr.context.get("policy", ""),
            **shared,
        )

    created = verify_mod.new_routes(cr.changeset)
    if created:
        cr.context["created_routes"] = created
        cr.context.setdefault("route", created[0])

    if span is not None:
        span.set_attribute("plan.created_routes", ",".join(created))
        span.set_attribute("plan.files_changed", len(cr.changeset))
        span.set_attribute("plan.paths", ",".join(cr.files_changed))
        span.set_attribute("plan.attempt", cr.attempts + 1)
        span.set_attribute("plan.rationale", getattr(cr.changeset, "rationale", "")[:400])
        span.set_attribute("plan.reasons", "; ".join(c.get("reason", "") for c in cr.changeset)[:400])
        span.set_attribute("plan.tokens_in", getattr(cr.changeset, "tokens_in", 0))
        span.set_attribute("plan.tokens_out", getattr(cr.changeset, "tokens_out", 0))
        span.set_attribute("plan.model", getattr(cr.changeset, "model", None) or "none")
        span.set_attribute("plan.test_included", getattr(cr.changeset, "test_included", False))
        if getattr(cr.changeset, "rejected_paths", None):
            span.add_event(
                "forge.plan.paths_refused",
                {"paths": ",".join(cr.changeset.rejected_paths)},
            )
    return cr


@_stage("ACT")
def act(cr: ChangeRequest, span) -> ChangeRequest:
    """Write the files to a git branch.

    Nothing is pushed here. A branch with failing tests must never leave the
    laptop, so the push lives after VERIFY, in GATE.
    """
    slug = (cr.check_id or "feat").lower() + "-" + cr.run_id
    cr.branch = vcs.create_branch(slug)
    written = vcs.write_files(cr.changeset)
    cr.context["written"] = written
    if span is not None:
        span.set_attribute("act.branch", cr.branch or "")
        span.set_attribute("act.files_written", len(written or []))
    return cr


@_stage("VERIFY")
def verify(cr: ChangeRequest, span) -> ChangeRequest:
    """Two independent checks: tests say it works, a fresh audit says it is not
    vulnerable. An introduced finding is a hard blocker, not a warning."""
    result = verify_mod.verify(cr.changeset, cr)
    cr.verify = result.as_dict()
    # ONE ENTRY PER ATTEMPT, appended and never overwritten. A run rejected three
    # times used to reach a human carrying only the third rejection, so finding
    # out that attempt 1 edited the wrong file meant reading an hour of logs.
    # This is what summary() publishes and what GET /api/runs/{id} serves.
    cr.verify_attempts.append(result.record())
    if span is not None:
        span.set_attribute("verify.ok", result.ok)
        span.set_attribute("verify.attempt", result.attempt)
        span.set_attribute("verify.tests_passed", result.tests_passed)
        span.set_attribute("verify.findings_closed", len(result.findings_closed))
        span.set_attribute("verify.findings_introduced", len(result.findings_introduced))
        span.set_attribute("verify.findings_still_open", len(result.findings_still_open))
        span.set_attribute("verify.rejected_reason", (result.rejected_reason or "")[:400])
        span.set_attribute("verify.evidence", (result.evidence or "")[:400])
        if not result.ok:
            span.add_event(
                "forge.verify.rejected",
                {"reason": (result.evidence or "verification failed")[:400]},
            )
    _safe(
        telemetry.counter,
        "forge_fix_attempts_total",
        1,
        intake=cr.intake,
        result="pass" if result.ok else "fail",
    )
    return cr


@_stage("GATE")
def gate(cr: ChangeRequest, span) -> ChangeRequest:
    """Push, open the pull request, and wait for a human. Identical for both loops."""
    message = "[" + cr.intake + "] " + cr.title + " (" + cr.run_id + ")"
    commit = vcs.commit_and_push(cr.branch, message)
    cr.context["commit"] = commit
    cr.pr_url = vcs.open_pr(cr.branch, cr.title, _pr_body(cr))

    cr.approval_id = _safe(portal.request_approval, cr)
    cr.approved = bool(_safe(portal.wait_for_approval, cr.approval_id))

    if span is not None:
        span.set_attribute("gate.pr_url", cr.pr_url or "")
        span.set_attribute("gate.commit", commit or "")
        span.set_attribute("gate.approval_id", cr.approval_id or "")
        span.set_attribute("gate.approved", cr.approved)
        span.add_event("forge.gate.decision", {"approved": cr.approved, "pr_url": cr.pr_url or ""})
    _safe(telemetry.counter, "forge_gate_total", 1, decision="approved" if cr.approved else "rejected")
    return cr


@_stage("RELEASE")
def release(cr: ChangeRequest, span) -> ChangeRequest:
    """Merge and let the app pick it up. The closing AUDIT confirms it."""
    merged = bool(vcs.merge_pr(cr.pr_url))
    cr.outcome = OUTCOME_MERGED if merged else OUTCOME_MERGE_FAILED
    cr.status = "done" if merged else "failed"
    if config.RELEASE_SETTLE_SECONDS:
        time.sleep(config.RELEASE_SETTLE_SECONDS)  # let uvicorn --reload catch up
    if span is not None:
        span.set_attribute("release.merged", merged)
        span.set_attribute("release.pr_url", cr.pr_url or "")
    _safe(telemetry.counter, "forge_fix_outcome_total", 1, result=cr.outcome)
    return cr


@_stage("ESCALATE")
def escalate(cr: ChangeRequest, span, reason: str | None = None) -> ChangeRequest:
    """The decline path. No code is written here, on purpose.

    This is the step that makes FORGE a factory with judgement rather than a
    loop: it records what it refused to do and why, in a form a human reads.
    """
    reason = reason or cr.justification or "declined by triage"
    cr.status = "escalated"

    if cr.classification == FALSE_POSITIVE and cr.finding:
        _safe(store.suppress_finding, cr.finding.get("finding_id"), reason)
        cr.outcome = OUTCOME_SUPPRESSED
    elif cr.classification == UPSTREAM_OUTAGE:
        cr.outcome = OUTCOME_BACKED_OFF
    elif cr.classification == DUPLICATE:
        cr.outcome = OUTCOME_DUPLICATE
    elif cr.outcome is None:
        cr.outcome = OUTCOME_ESCALATED

    cr.context["escalation_reason"] = reason
    cr.context["escalation_id"] = _safe(portal.escalate, cr, reason)

    if span is not None:
        span.set_attribute("escalate.reason", reason[:400])
        span.set_attribute("escalate.classification", cr.classification or "")
        span.set_attribute("escalate.outcome", cr.outcome)
        span.set_attribute("escalate.wrote_code", False)
        span.add_event("forge.decision.declined", {"outcome": cr.outcome, "reason": reason[:400]})
    _safe(telemetry.counter, "forge_fix_outcome_total", 1, result=cr.outcome)
    log.info("ESCALATE %s -> %s: %s", cr.run_id, cr.outcome, reason)
    return cr


@_stage("AUDIT", span_name="forge.close_out")
def close_out(cr: ChangeRequest, span) -> ChangeRequest:
    """AUDIT closes every run and writes the record -- declined runs included."""
    routes = _affected_routes(cr)
    result = _safe(audit_mod.run_audit, config.PULSE_BASE_URL, routes)

    if result is not None:
        _safe(store.save_findings, cr.run_id, result.findings, result.routes_checked)
        _safe(store.save_audit, result)
        for route, grade in (result.grades or {}).items():
            _safe(portal.update_scorecard, route, grade, result.for_route(route))
        cr.context["closing_audit"] = result.as_dict()
        if span is not None:
            span.set_attribute("audit.routes_checked", len(result.routes_checked))
            span.set_attribute("audit.findings_total", len(result.findings))
            span.set_attribute("audit.findings_high", len(result.findings_high))
            span.set_attribute("audit.grade_worst", result.worst_grade)

    if cr.outcome is None:
        cr.outcome = OUTCOME_ESCALATED if not cr.should_act else OUTCOME_MERGED
    if cr.status not in ("escalated", "failed", "rejected"):
        cr.status = "done"
    cr.finished_at = time.time()

    if span is not None:
        span.set_attribute("forge.outcome", cr.outcome)
        span.set_attribute("forge.duration_ms", cr.duration_ms)
    _safe(telemetry.counter, "forge_runs_closed_total", 1, outcome=cr.outcome, intake=cr.intake)
    log.info("CLOSED %s outcome=%s in %sms", cr.run_id, cr.outcome, cr.duration_ms)
    return cr


# --------------------------------------------------------------------------
# the machine
# --------------------------------------------------------------------------
def run(cr: ChangeRequest) -> ChangeRequest:
    """Drive one ChangeRequest through the eight steps.

    One parent span, the steps nested underneath it. Read top to bottom, this
    function IS the architecture claim -- there is no branch on intake type
    anywhere in it.
    """
    started_monotonic = time.monotonic()
    run_timeout_seconds = float(config.FORGE_RUN_TIMEOUT_SECONDS)
    with telemetry.stage_span("forge.run", cr.run_id) as root:
        _tag(root, cr)
        try:
            if time.monotonic() - started_monotonic >= run_timeout_seconds:
                raise TimeoutError(f"run exceeded wall-clock timeout of {run_timeout_seconds}s")
            cr = intake(cr)
            if time.monotonic() - started_monotonic >= run_timeout_seconds:
                raise TimeoutError(f"run exceeded wall-clock timeout of {run_timeout_seconds}s")
            cr = context(cr)
            if time.monotonic() - started_monotonic >= run_timeout_seconds:
                raise TimeoutError(f"run exceeded wall-clock timeout of {run_timeout_seconds}s")
            cr = triage(cr)

            # The decline path. A clean early return: no PLAN, no ACT, no
            # VERIFY, no GATE. Nothing is written when we choose not to act.
            if not cr.should_act:
                cr = escalate(cr)
                return close_out(cr)

            while True:
                if time.monotonic() - started_monotonic >= run_timeout_seconds:
                    raise TimeoutError(f"run exceeded wall-clock timeout of {run_timeout_seconds}s")
                try:
                    cr = plan(cr)
                except (planner.PlannerUnavailable, llm.BudgetExceeded) as exc:
                    cr.outcome = OUTCOME_ESCALATED
                    return close_out(escalate(cr, f"No patch could be generated: {exc}"))
                cr = act(cr)
                cr = verify(cr)
                if cr.verify.get("ok"):
                    break

                cr.attempts += 1
                if cr.attempts >= MAX_PLAN_ATTEMPTS:
                    cr.outcome = OUTCOME_VERIFY_FAILED
                    reason = (
                        "Verification failed " + str(cr.attempts) + " times, escalating rather than "
                        "shipping. Last failure: " + str(cr.verify.get("evidence"))
                    )
                    cr = escalate(cr, reason)
                    return close_out(cr)

                # Loop back to PLAN with strictly more information than last
                # time: WHICH FILES the attempt wrote, the exact rejection, and
                # whether the finding survived it. Attempt 2 repeating attempt
                # 1's mistake is a prompt that did not carry the mistake.
                still_open = cr.verify.get("findings_still_open") or []
                target_id = (cr.finding or {}).get("finding_id")
                cr.context["previous_attempt"] = {
                    "attempt": cr.attempts,
                    "changeset": list(cr.changeset),
                    "paths": list(cr.files_changed),
                    "verify": cr.verify,
                    "finding_still_open": (
                        any(f.get("finding_id") == target_id for f in still_open)
                        if target_id else None
                    ),
                    "family_still_open": [f.get("check_id") for f in still_open],
                }
                cr.context["verify_failure"] = cr.verify.get("evidence")
                log.info(
                    "VERIFY rejected %s attempt %s: %s (wrote %s)",
                    cr.run_id, cr.attempts, cr.verify.get("rejected_reason"),
                    ", ".join(cr.files_changed) or "nothing",
                )
                log.info("VERIFY failed for %s, retry %s of %s", cr.run_id, cr.attempts, MAX_PLAN_ATTEMPTS - 1)

            if time.monotonic() - started_monotonic >= run_timeout_seconds:
                raise TimeoutError(f"run exceeded wall-clock timeout of {run_timeout_seconds}s")
            cr = gate(cr)
            if not cr.approved:
                cr.outcome = OUTCOME_REJECTED
                cr.status = "rejected"
                return close_out(cr)

            cr = release(cr)
            return close_out(cr)

        except Exception as exc:
            cr.outcome = OUTCOME_ERROR
            cr.status = "failed"
            cr.finished_at = time.time()
            cr.context["error"] = str(exc)
            if root is not None:
                try:
                    root.record_exception(exc)
                except Exception:
                    pass
            log.error("run %s failed at %s: %s\n%s", cr.run_id, cr.stage, exc, traceback.format_exc())
            _upsert(cr)
            _safe(telemetry.counter, "forge_runs_closed_total", 1, outcome=OUTCOME_ERROR, intake=cr.intake)
            if config.ENGINE_RAISES:
                raise
            return cr


# --------------------------------------------------------------------------
# entry points -- what the scheduler, the webhooks and my scripts call
# --------------------------------------------------------------------------
def run_from_finding(finding: dict) -> ChangeRequest:
    """Loop B. A SigNoz alert, or the scheduler grade check, lands here."""
    finding = finding or {}
    title = finding.get("title") or (str(finding.get("check_id")) + " on " + str(finding.get("route")))
    return run(
        ChangeRequest(
            run_id=new_run_id(),
            intake=INTAKE_FINDING,
            title=title,
            finding=finding,
        )
    )


def run_from_brief(text: str, title: str | None = None, run_id: str | None = None) -> ChangeRequest:
    """Loop A. A brief submitted in Port lands here.

    run_id is accepted so the intake endpoint can hand the caller a handle
    before the run finishes -- the same id that lands in Port and on the trace.
    """
    first_line = (text or "").strip().split("\n")[0][:80]
    return run(
        ChangeRequest(
            run_id=run_id or new_run_id(),
            intake=INTAKE_BRIEF,
            title=title or first_line or "Untitled brief",
            brief_text=text,
        )
    )

"""
tests/test_engine.py -- the invariants everything else depends on.

These are not tests of the stubs. They are tests of the four claims we make to
a judge about the engine, so they must keep passing as the stubs turn real.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from forge import engine
from forge.models import (
    AUTOFIX_SAFE,
    AuditResult,
    ChangeSet,
    TriageResult,
    VerifyResult,
    FALSE_POSITIVE,
    NEEDS_HUMAN_DESIGN,
    NEW_FEATURE,
    OUTCOME_BACKED_OFF,
    OUTCOME_MERGED,
    OUTCOME_SUPPRESSED,
    OUTCOME_VERIFY_FAILED,
    UPSTREAM_OUTAGE,
)

def _fake_triage(finding, page_source, file_context, history, **kw):
    """A deterministic stand-in for triage, so engine tests are hermetic.

    Whether these classifications are the RIGHT ones is triage's problem and is
    tested in tests/test_triage.py against the real prompt and rails. What the
    engine owes us is that it routes each one correctly, which needs a triage
    that answers the same way every time and never calls an API.
    """
    finding = finding or {}
    check_id = finding.get("check_id")
    if check_id == "BRIEF":
        return TriageResult(NEW_FEATURE, True, "Coherent and in scope.", 0.8, "contained", decided_by="stub")
    if finding.get("reachable") is False or not (page_source or "").strip():
        return TriageResult(UPSTREAM_OUTAGE, False, "Nothing was served.", 1.0, "unknown", decided_by="stub")
    if check_id in ("S8", "P1"):
        return TriageResult(NEEDS_HUMAN_DESIGN, False, "Blast radius reaches clients.", 0.8, "clients", decided_by="stub")
    return TriageResult(AUTOFIX_SAFE, True, "Contained to one file.", 0.8, "contained", decided_by="stub")


def _fake_verify(changeset, cr):
    """Verification always succeeds here. Whether it SHOULD succeed is verify's
    problem and is tested in tests/test_verify.py -- against the real rails,
    with real before/after audits. What the engine owes us is that it routes a
    pass to the gate and a failure back to PLAN."""
    return VerifyResult(ok=True, tests_passed=True, evidence="stubbed verification")


def _failing_verify(changeset, cr):
    return VerifyResult(ok=False, tests_passed=False, evidence="stubbed failure: assert 404 == 200")


def _fake_plan(*args, **kwargs):
    return ChangeSet(
        [
            {"path": "pulse/main.py", "content": "app = FastAPI(docs_url=None)", "reason": "the fix"},
            {"path": "tests/test_generated.py", "content": "def test_x(): assert True", "reason": "test"},
        ],
        rationale="stubbed plan",
        test_included=True,
    )


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """The engine tests test the engine -- not the audit, triage or the planner.

    Every collaborator that would make a network call is replaced with a
    deterministic double. Without this the suite is slow, costs money if a key
    happens to be exported, and fails for reasons that have nothing to do with
    the engine. Each of those three has its own test file.
    """
    monkeypatch.setattr(
        "forge.engine.audit_mod.run_audit",
        lambda base_url=None, routes=None, **kw: AuditResult(
            base_url=base_url or "", routes_checked=list(routes or ["/"]), grades={}
        ),
    )
    monkeypatch.setattr("forge.engine.triage_mod.classify", _fake_triage)
    monkeypatch.setattr("forge.engine.planner.plan_fix", _fake_plan)
    monkeypatch.setattr("forge.engine.planner.plan_feature", _fake_plan)
    monkeypatch.setattr("forge.engine.verify_mod.verify", _fake_verify)


FINDING = {
    "finding_id": "f_test",
    "check_id": "S9",
    "severity": "HIGH",
    "route": "/stock-alerts",
    "title": "API documentation endpoint reachable in production mode",
    "evidence": "GET /docs returned 200 with a full OpenAPI schema",
    "suggested_fix_hint": "Guard the docs route",
    "page_source": "<html><body>alerts</body></html>",
}


def _finding(**overrides):
    return dict(FINDING, **overrides)


def test_a_finding_runs_end_to_end_and_merges():
    cr = engine.run_from_finding(_finding())
    assert cr.classification == AUTOFIX_SAFE
    assert cr.should_act is True
    assert cr.outcome == OUTCOME_MERGED
    assert cr.pr_url, "every change ships through a pull request"
    assert cr.branch, "the factory never commits to main"


def test_a_brief_runs_the_same_path():
    cr = engine.run_from_brief("Add a page showing out-of-stock products, sorted by price descending.")
    assert cr.intake == "brief"
    assert cr.classification == NEW_FEATURE
    assert cr.outcome == OUTCOME_MERGED
    assert cr.pr_url, "Loop A gates on a human exactly like Loop B"


def test_declining_writes_no_code_and_opens_no_pr():
    """The headline feature. A declined run must not plan, act or gate."""
    cr = engine.run_from_finding(_finding(check_id="S8", route="/products"))
    assert cr.classification == NEEDS_HUMAN_DESIGN
    assert cr.should_act is False
    assert cr.changeset == [], "no code is written when we decline"
    assert cr.branch is None, "no branch is created when we decline"
    assert cr.pr_url is None, "no pull request is opened when we decline"
    assert cr.justification, "a decline must carry a written justification"


def test_an_unreachable_target_is_an_outage_not_seventeen_defects():
    cr = engine.run_from_finding(_finding(reachable=False, page_source=""))
    assert cr.classification == UPSTREAM_OUTAGE
    assert cr.should_act is False
    assert cr.outcome == OUTCOME_BACKED_OFF
    assert cr.changeset == []


def test_a_false_positive_is_suppressed_with_a_reason(monkeypatch):
    """Whether an SRI hash IS a false positive is triage's call and is tested in
    tests/test_triage.py. What the ENGINE owes us is that a FALSE_POSITIVE gets
    suppressed with its reason carried into the catalog, and that no code runs."""
    reason = "The matched string is a Subresource Integrity hash, not a credential."
    monkeypatch.setattr(
        "forge.engine.triage_mod.classify",
        lambda *a, **k: TriageResult(FALSE_POSITIVE, False, reason, 0.85, "contained"),
    )
    suppressed = {}
    monkeypatch.setattr("forge.store.suppress_finding", lambda fid, why: suppressed.update({fid: why}))

    cr = engine.run_from_finding(_finding(check_id="S10"))
    assert cr.classification == FALSE_POSITIVE
    assert cr.outcome == OUTCOME_SUPPRESSED
    assert cr.changeset == [], "a dismissed finding must not produce code"
    assert suppressed == {"f_test": reason}, "the reason is written where a human will read it"


def test_verify_failure_retries_twice_then_escalates(monkeypatch):
    """Two retries, then escalate. It must never ship an unverified change."""
    monkeypatch.setattr("forge.engine.verify_mod.verify", _failing_verify)
    cr = engine.run_from_finding(_finding(route="/retry-demo"))
    assert cr.attempts == engine.MAX_PLAN_ATTEMPTS == 3
    assert cr.outcome == OUTCOME_VERIFY_FAILED
    assert cr.approved is None, "an unverified change never reaches the human gate"


def test_a_human_saying_no_stops_the_merge(monkeypatch):
    monkeypatch.setattr("forge.portal.wait_for_approval", lambda approval_id: False)
    cr = engine.run_from_finding(_finding())
    assert cr.approved is False
    assert cr.outcome == "rejected_by_human"
    assert cr.status == "rejected"


def test_every_run_closes_with_an_audit_record():
    for cr in (
        engine.run_from_finding(_finding()),
        engine.run_from_finding(_finding(check_id="S8")),
        engine.run_from_brief("Add a JSON API endpoint returning all products."),
    ):
        assert cr.stage == "AUDIT", "AUDIT closes every run, declined ones included"
        assert cr.trace_id, "every run carries a trace id for the SigNoz deep link"
        assert cr.finished_at is not None

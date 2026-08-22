"""
tests/test_verify.py -- the gate.

The failure this file exists to prevent: a patch that passes its test without
closing the hole, or closes the hole while opening another, reaching a human
with a green tick on it. Everything else here is secondary.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from forge import verify as verifier
from forge.models import AuditResult, ChangeRequest, INTAKE_BRIEF, INTAKE_FINDING

TARGET = {"finding_id": "f_target", "check_id": "S12", "route": "/products", "severity": "MED",
          "title": "docs open", "evidence": "GET /docs returned 200"}
NEW_HIGH = {"finding_id": "f_new", "check_id": "S8", "route": "/products", "severity": "HIGH",
            "title": "CORS wildcard", "evidence": "ACAO * with credentials"}
NEW_MED = {"finding_id": "f_med", "check_id": "S3", "route": "/products", "severity": "MED",
           "title": "no HSTS", "evidence": "absent"}

CHANGESET = [
    {"path": "pulse/main.py", "content": '@app.get("/products")\ndef p(): ...', "reason": "fix"},
    {"path": "tests/test_products.py", "content": "def test_p(): assert True", "reason": "test"},
]


def _cr(intake=INTAKE_FINDING, finding=None):
    return ChangeRequest(run_id="run_test", intake=intake, title="t", finding=finding)


def _audit(findings, reachable=True):
    return AuditResult(findings=list(findings), reachable=reachable, routes_checked=["/products"])


@pytest.fixture
def green_tests(monkeypatch):
    monkeypatch.setattr(verifier, "run_tests", lambda changeset, cwd=None: (True, "2 passed"))


def _wire(monkeypatch, before, after, boot_error=None):
    """Stand in for the baseline audit, the candidate app and its fresh audit."""
    from contextlib import contextmanager

    @contextmanager
    def fake_serve(port=None):
        yield (None, boot_error) if boot_error else ("http://127.0.0.1:9999", None)

    monkeypatch.setattr(verifier, "serve_candidate", fake_serve)
    monkeypatch.setattr(verifier, "_baseline", lambda routes: before)
    monkeypatch.setattr("forge.audit.run_audit", lambda base_url=None, routes=None, **kw: after)


# ------------------------------------------------------- the headline rail --
def test_a_patch_that_closes_one_hole_and_opens_another_is_rejected(monkeypatch, green_tests):
    _wire(monkeypatch, before=_audit([TARGET]), after=_audit([NEW_HIGH]))
    result = verifier.verify(CHANGESET, _cr(finding=TARGET))
    assert result.ok is False, "introduced HIGH findings are a hard blocker"
    assert result.tests_passed is True, "the tests passed -- that is exactly the point"
    assert result.findings_introduced == ["f_new"]
    assert "closes one hole and opens another" in result.evidence


def test_a_patch_that_passes_tests_without_closing_the_finding_is_rejected(monkeypatch, green_tests):
    """The agent satisfied its own test and left the hole open."""
    _wire(monkeypatch, before=_audit([TARGET]), after=_audit([TARGET]))
    result = verifier.verify(CHANGESET, _cr(finding=TARGET))
    assert result.ok is False
    assert result.tests_passed is True
    assert "STILL PRESENT" in result.evidence
    assert result.findings_closed == []


def test_a_real_fix_verifies(monkeypatch, green_tests):
    _wire(monkeypatch, before=_audit([TARGET]), after=_audit([]))
    result = verifier.verify(CHANGESET, _cr(finding=TARGET))
    assert result.ok is True
    assert result.findings_closed == ["f_target"]
    assert result.findings_introduced == []
    assert "VERDICT: verified" in result.evidence


def test_a_new_med_is_reported_but_does_not_block(monkeypatch, green_tests):
    """The stated rule is no new HIGH. A new MED is worth saying, not blocking."""
    _wire(monkeypatch, before=_audit([TARGET]), after=_audit([NEW_MED]))
    result = verifier.verify(CHANGESET, _cr(finding=TARGET))
    assert result.ok is True
    assert result.findings_introduced == []
    assert "Also new, not blocking" in result.evidence


# ------------------------------------------------------------ check one --
def test_failing_tests_stop_the_run_before_the_audit(monkeypatch):
    monkeypatch.setattr(verifier, "run_tests",
                        lambda changeset, cwd=None: (False, "test_products.py::test_p FAILED\nassert 404 == 200"))
    audited = []
    monkeypatch.setattr("forge.audit.run_audit", lambda *a, **k: audited.append(1))
    result = verifier.verify(CHANGESET, _cr(finding=TARGET))
    assert result.ok is False
    assert result.tests_passed is False
    assert "assert 404 == 200" in result.evidence, "the actual output is the evidence"
    assert audited == [], "do not audit a change that fails its own tests"


def test_a_change_with_no_test_cannot_be_verified():
    passed, output = verifier.run_tests([{"path": "pulse/main.py", "content": "x", "reason": "r"}])
    assert passed is False
    assert "nothing to run" in output


def test_the_factorys_own_suite_is_not_the_apps_suite():
    """Scope is the changeset's tests. A failing forge test must not block an
    app patch -- they are different suites sharing a folder."""
    assert verifier._test_paths(CHANGESET) == ["tests/test_products.py"]


# ------------------------------------------------------------ check two --
def test_a_patch_that_stops_the_app_booting_fails_verification(monkeypatch, green_tests):
    """Caught before the pull request exists, not after the merge."""
    _wire(monkeypatch, before=_audit([TARGET]), after=None,
          boot_error='the patched app did not start within 20s on port 9999\nSyntaxError: invalid syntax')
    result = verifier.verify(CHANGESET, _cr(finding=TARGET))
    assert result.ok is False
    assert result.tests_passed is True
    assert "SyntaxError" in result.evidence
    assert "cannot be measured does not ship" in result.evidence


def test_no_baseline_tightens_the_rule_instead_of_relaxing_it(monkeypatch, green_tests):
    """An unreachable baseline would otherwise report phantom findings as
    CLOSED and pass a patch that fixed nothing."""
    _wire(monkeypatch, before=_audit([TARGET], reachable=False), after=_audit([NEW_HIGH]))
    result = verifier.verify(CHANGESET, _cr(finding=TARGET))
    assert result.ok is False
    assert result.findings_introduced == ["f_new"]
    assert "stricter rule applies" in result.evidence


def test_no_baseline_still_passes_a_genuinely_clean_result(monkeypatch, green_tests):
    _wire(monkeypatch, before=None, after=_audit([]))
    result = verifier.verify(CHANGESET, _cr(finding=TARGET))
    assert result.ok is True
    assert result.audit_before == {}


# ------------------------------------------------------- the feature run --
FEATURE = [
    {"path": "pulse/routes/stock.py",
     "content": '@router.get("/stock-alerts")\ndef stock(): ...', "reason": "the new route"},
    {"path": "tests/test_stock.py", "content": "def test_s(): assert True", "reason": "test"},
]


def test_a_generated_route_with_a_high_finding_does_not_ship(monkeypatch, green_tests):
    dirty = {"finding_id": "f_gen", "check_id": "S1", "route": "/stock-alerts", "severity": "HIGH",
             "title": "no CSP", "evidence": "absent"}
    _wire(monkeypatch, before=_audit([]), after=_audit([dirty]))
    result = verifier.verify(FEATURE, _cr(intake=INTAKE_BRIEF))
    assert result.ok is False
    assert "/stock-alerts" in result.evidence
    assert "acceptance criteria" in result.evidence


def test_a_clean_generated_route_verifies(monkeypatch, green_tests):
    _wire(monkeypatch, before=_audit([]), after=_audit([]))
    result = verifier.verify(FEATURE, _cr(intake=INTAKE_BRIEF))
    assert result.ok is True
    assert "no HIGH findings" in result.evidence


def test_the_route_is_read_out_of_the_generated_file(monkeypatch, green_tests):
    """The factory is not told which route it just wrote -- it reads the
    decorator it generated."""
    audited_routes = {}
    _wire(monkeypatch, before=_audit([]), after=_audit([]))
    monkeypatch.setattr("forge.audit.run_audit",
                        lambda base_url=None, routes=None, **kw: audited_routes.setdefault("routes", routes) and None or _audit([]))
    verifier.verify(FEATURE, _cr(intake=INTAKE_BRIEF))
    assert "/stock-alerts" in audited_routes["routes"]
    assert "/" in audited_routes["routes"], "app-level checks ride on the root"


# ------------------------------------------------- what the engine reads --
def test_the_result_carries_what_the_span_and_the_pr_body_need(monkeypatch, green_tests):
    _wire(monkeypatch, before=_audit([TARGET]), after=_audit([]))
    result = verifier.verify(CHANGESET, _cr(finding=TARGET))
    as_dict = result.as_dict()
    for field in ("ok", "tests_passed", "audit_before", "audit_after", "findings_closed",
                  "findings_introduced", "evidence"):
        assert field in as_dict
    assert as_dict["audit_after"]["findings_total"] == 0
    assert as_dict["audit_before"]["findings_total"] == 1


def test_evidence_does_not_claim_a_finding_is_new_when_there_is_no_baseline(monkeypatch, green_tests):
    """Without a before, we can say a finding is PRESENT, not that it is NEW.
    The evidence goes on the pull request; it must not overstate what we know."""
    _wire(monkeypatch, before=None, after=_audit([NEW_HIGH]))
    result = verifier.verify(CHANGESET, _cr(finding=TARGET))
    assert result.ok is False
    assert "still serves 1 HIGH finding" in result.evidence
    assert "were not there before" not in result.evidence


def test_evidence_does_claim_it_when_there_is_a_baseline(monkeypatch, green_tests):
    _wire(monkeypatch, before=_audit([TARGET]), after=_audit([NEW_HIGH]))
    result = verifier.verify(CHANGESET, _cr(finding=TARGET))
    assert "were not there before" in result.evidence

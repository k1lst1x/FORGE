"""
tests/test_audit.py -- the audit engine.

Two layers. Synthetic responses pin each check's exact firing condition, and a
live run against tests/fixtures/insecure_app.py proves the checks are actually
wired to a real HTTP response. The second layer is the one that matters: an
audit that returns nothing because it never looked at the body would pass a
unit test and fail the demo.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from forge import audit
from forge.audit import Fetched
from forge.models import BRONZE, GOLD, SILVER, grade_for

POLICY = audit.load_policy()
FIXTURE_URL = "http://127.0.0.1:8199"

SECURE_HEADERS = {
    "content-security-policy": "default-src 'self'; frame-ancestors 'none'",
    "x-frame-options": "DENY",
    "strict-transport-security": "max-age=63072000",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "server": "uvicorn",
}


def _resp(headers=None, body="<html><head><title>T</title></head><body></body></html>", **kw):
    return Fetched(
        route=kw.pop("route", "/"),
        url="http://test/",
        status=200,
        headers=headers if headers is not None else dict(SECURE_HEADERS),
        body=body,
        ok=True,
        **kw,
    )


# ---------------------------------------------------------------- policy ----
def test_policy_has_all_seventeen_checks():
    assert len(POLICY["checks"]) == 17
    ids = {c["id"] for c in POLICY["checks"]}
    assert ids == {f"S{n}" for n in range(1, 13)} | {"Q1", "Q2", "Q3", "Q4", "P1"}
    for check in POLICY["checks"]:
        assert check["severity"] in ("HIGH", "MED", "LOW")
        assert check["category"] in ("security", "quality", "performance")
        assert check["fix_hint"].strip(), f"{check['id']} needs a fix hint"


def test_the_two_escalate_checks_are_marked_as_such():
    """S8 and P1 must never be auto-patched. The policy is where that is decided."""
    escalate = {c["id"] for c in POLICY["checks"] if c["action"] == "escalate"}
    assert escalate == {"S8", "P1"}


# ---------------------------------------------------- headers, S1 to S8 ----
def test_a_properly_secured_response_produces_no_header_findings():
    assert audit.check_headers(_resp(), POLICY) == []


def test_a_bare_response_fires_the_header_checks():
    fired = {f["check_id"] for f in audit.check_headers(_resp(headers={}), POLICY)}
    assert {"S1", "S2", "S3", "S4", "S5"} <= fired


def test_csp_frame_ancestors_satisfies_s2_without_x_frame_options():
    headers = dict(SECURE_HEADERS)
    del headers["x-frame-options"]
    assert "S2" not in {f["check_id"] for f in audit.check_headers(_resp(headers=headers), POLICY)}


def test_s6_fires_only_on_a_version_bearing_header():
    plain = audit.check_headers(_resp(headers=dict(SECURE_HEADERS, server="uvicorn")), POLICY)
    assert "S6" not in {f["check_id"] for f in plain}
    versioned = audit.check_headers(_resp(headers=dict(SECURE_HEADERS, server="Werkzeug/2.3.7")), POLICY)
    assert "S6" in {f["check_id"] for f in versioned}


def test_s7_fires_on_a_cookie_missing_flags_and_names_it_without_the_value():
    findings = audit.check_headers(_resp(cookies=["session=super-secret-value; Path=/"]), POLICY)
    s7 = [f for f in findings if f["check_id"] == "S7"]
    assert s7, "a cookie with no flags must fire S7"
    assert "super-secret-value" not in s7[0]["evidence"], "never log a cookie value"
    assert "session" in s7[0]["evidence"]


def test_s7_passes_when_the_cookie_is_set_properly():
    good = ["session=x; Path=/; Secure; HttpOnly; SameSite=lax"]
    assert "S7" not in {f["check_id"] for f in audit.check_headers(_resp(cookies=good), POLICY)}


def test_s8_needs_credentials_as_well_as_a_wildcard():
    wildcard_only = dict(SECURE_HEADERS, **{"access-control-allow-origin": "*"})
    assert "S8" not in {f["check_id"] for f in audit.check_headers(_resp(headers=wildcard_only), POLICY)}
    with_creds = dict(wildcard_only, **{"access-control-allow-credentials": "true"})
    assert "S8" in {f["check_id"] for f in audit.check_headers(_resp(headers=with_creds), POLICY)}


# ------------------------------------------------------- secrets, S10 ----
def test_s10_finds_a_key_and_never_prints_it():
    key = "sk-proj0aB1cD2eF3gH4iJ5kL6mN7oP8qR9sT0u"
    findings = audit.check_secrets(_resp(body=f"<html><!-- {key} --></html>"), POLICY)
    assert findings, "a key-shaped string in the HTML must fire S10"
    evidence = findings[0]["evidence"]
    assert key not in evidence, "the scanner must not leak what it found"
    assert "redacted" in evidence and "sk-p" in evidence


def test_s10_reports_the_surrounding_context_so_a_human_can_find_it():
    body = '<html><script>const token = "AKIAIOSFODNN7EXAMPLE";</script></html>'
    evidence = audit.check_secrets(_resp(body=body), POLICY)[0]["evidence"]
    assert "const token" in evidence
    assert "AKIAIOSFODNN7EXAMPLE" not in evidence


def test_s10_fires_on_an_sri_hash_which_is_the_false_positive_triage_must_catch():
    """This is not a bug. A scanner that ignores base64 blobs misses real keys.
    Deciding an SRI hash is harmless is triage's job, and it writes down why."""
    body = '<script src="/x.js" integrity="sha384-' + "a" * 60 + '"></script>'
    assert audit.check_secrets(_resp(body=body), POLICY), "SRI hash should fire S10"


def test_clean_html_produces_no_secret_findings():
    assert audit.check_secrets(_resp(body="<html><p>Widget A costs $49.00</p></html>"), POLICY) == []


# --------------------------------------------------- performance, P1 ----
def test_p1_fires_over_the_budget_and_not_under():
    assert audit.check_performance(_resp(elapsed_ms=120.0), POLICY) == []
    slow = audit.check_performance(_resp(elapsed_ms=910.0), POLICY)
    assert slow and slow[0]["check_id"] == "P1"
    assert "910.0ms" in slow[0]["evidence"]


# ------------------------------------------------------------ grading ----
def test_the_three_grades():
    assert grade_for([]) == GOLD
    assert grade_for([{"severity": "LOW"}]) == GOLD
    assert grade_for([{"severity": "MED"}]) == SILVER
    assert grade_for([{"severity": "HIGH"}, {"severity": "MED"}]) == BRONZE


def test_finding_ids_are_stable_across_runs():
    """Dedupe by check_id + route and the occurrence count both depend on this."""
    assert audit._finding_id("S9", "/products") == audit._finding_id("S9", "/products")
    assert audit._finding_id("S9", "/products") != audit._finding_id("S9", "/")


# ------------------------------------------------------------- outage ----
def test_an_unreachable_target_reports_unreachable_rather_than_crashing():
    """Mode 4. Every check fails because nothing is there -- and the result says
    so, which is what lets triage call it an outage instead of 17 defects."""
    result = audit.run_audit("http://127.0.0.1:9", ["/"])
    assert result.reachable is False
    assert result.findings, "a dead target still produces findings, naively"
    assert all(f["reachable"] is False for f in result.findings)
    assert result.pages == {"/": ""}


# --------------------------------------------------------------- live ----
def _fixture_running() -> bool:
    try:
        return httpx.get(FIXTURE_URL + "/", timeout=2).status_code == 200
    except Exception:
        return False


live = pytest.mark.skipif(not _fixture_running(), reason="fixture app not running on 8199")


@live
def test_live_audit_finds_real_problems_in_a_real_app():
    """The check that matters. If this returns nothing the checks are not wired
    to the response and everything downstream is theatre."""
    result = audit.run_audit(FIXTURE_URL, ["/", "/products"])
    fired = {f["check_id"] for f in result.findings}
    assert len(result.findings_high) >= 5, "a plain FastAPI app should fail several HIGH checks"
    assert {"S1", "S2", "S9", "S10", "S12", "Q1", "Q3", "Q4"} <= fired
    assert result.worst_grade == BRONZE
    assert result.grades == {"/": BRONZE, "/products": BRONZE}


@live
def test_live_evidence_is_factual_and_specific():
    result = audit.run_audit(FIXTURE_URL, ["/"])
    by_check = {f["check_id"]: f for f in result.findings}
    assert "OpenAPI schema listing" in by_check["S12"]["evidence"]
    assert "/admin returned 200" in by_check["S9"]["evidence"]
    assert "Traceback" in by_check["S11"]["evidence"]
    assert "/static/logo.png" in by_check["Q1"]["evidence"]
    for finding in result.findings:
        assert finding["evidence"].strip(), f"{finding['check_id']} has empty evidence"
        assert finding["suggested_fix_hint"].strip()


@live
def test_app_level_findings_land_once_not_once_per_page():
    result = audit.run_audit(FIXTURE_URL, ["/", "/products", "/admin"])
    for check_id in ("S9", "S11", "S12"):
        hits = [f for f in result.findings if f["check_id"] == check_id]
        assert len(hits) <= 1, f"{check_id} is an app-level defect, not one per page"

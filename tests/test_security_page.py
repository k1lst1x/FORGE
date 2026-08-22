"""
tests/test_security_page.py -- the screen the judge watches.

Two things must hold. The page must never show green for a state that is not
green -- an unreachable factory and a clean app look identical if you only
render a list. And the page must survive its own audit: a security dashboard
that fails the security checks is the worst possible screenshot.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pulse.routes import security

HIGH = {"finding_id": "f_1", "check_id": "S9", "severity": "HIGH", "route": "/",
        "title": "Sensitive paths unreachable", "evidence": "GET /admin returned 200", "status": "open"}
MED = {"finding_id": "f_2", "check_id": "S3", "severity": "MED", "route": "/products",
       "title": "No HSTS", "evidence": "header absent", "status": "open"}
DISMISSED = {"finding_id": "f_3", "check_id": "S10", "severity": "HIGH", "route": "/",
             "title": "Secret-shaped string", "evidence": "sha384 hash", "status": "suppressed"}


def _client(payload):
    security._fetch = lambda: payload
    app = FastAPI()
    app.include_router(security.router)
    return TestClient(app)


def _page(payload) -> str:
    response = _client(payload).get("/security")
    assert response.status_code == 200
    return response.text


CLEAN = {"audited": True, "reachable": True, "base_url": "http://localhost:8100", "duration_ms": 412,
         "worst_grade": "gold", "routes": [{"route": "/", "grade": "gold", "grade_value": 3,
                                            "counts": {"HIGH": 0, "MED": 0, "LOW": 0}}],
         "findings": [], "totals": {"HIGH": 0, "MED": 0, "LOW": 0}, "open_count": 0, "suppressed_count": 0}

DIRTY = {"audited": True, "reachable": True, "base_url": "http://localhost:8100", "duration_ms": 530,
         "worst_grade": "bronze", "routes": [{"route": "/", "grade": "bronze", "grade_value": 1,
                                              "counts": {"HIGH": 1, "MED": 0, "LOW": 0}}],
         "findings": [HIGH, MED, DISMISSED], "totals": {"HIGH": 1, "MED": 1, "LOW": 0},
         "open_count": 2, "suppressed_count": 1}


# ------------------------------------------------------- the colour states --
def test_clean_is_green_and_says_so():
    page = _page(CLEAN)
    assert 'data-state="clean"' in page
    assert "ALL CLEAR" in page


def test_a_high_finding_is_critical():
    page = _page(DIRTY)
    assert 'data-state="critical"' in page
    assert "ACTION NEEDED" in page


def test_medium_only_is_a_warning_not_a_crisis():
    payload = dict(DIRTY, totals={"HIGH": 0, "MED": 2, "LOW": 1}, findings=[MED])
    page = _page(payload)
    assert 'data-state="warning"' in page
    assert "MINOR ISSUES" in page


def test_never_audited_is_not_green():
    """The distinction the whole page hinges on. Green must mean checked."""
    page = _page({"audited": False, "routes": [], "findings": [], "totals": {}})
    assert 'data-state="pending"' in page
    assert "NOT YET AUDITED" in page
    assert "not the same as being clean" in page
    assert "ALL CLEAR" not in page


def test_an_unreachable_factory_is_not_green_either():
    page = _page({"unreachable": True, "error": "ConnectError: refused", "audited": False})
    assert 'data-state="unreachable"' in page
    assert "FACTORY UNREACHABLE" in page
    assert "ALL CLEAR" not in page
    assert "ConnectError" in page, "say what actually went wrong"


def test_an_app_that_served_nothing_reads_as_an_outage():
    page = _page(dict(CLEAN, reachable=False))
    assert 'data-state="outage"' in page
    assert "TARGET DOWN" in page


# ------------------------------------------------------------- the content --
def test_every_finding_shows_severity_route_evidence_and_status():
    page = _page(DIRTY)
    for expected in ("S9", "HIGH", "/admin returned 200", "Sensitive paths unreachable",
                     "S3", "/products", "open"):
        assert expected in page, expected


def test_a_dismissed_finding_is_shown_as_dismissed_not_hidden():
    """A suppression a human can audit is the point of suppressing it."""
    page = _page(DIRTY)
    assert "suppressed" in page
    assert "Secret-shaped string" in page


def test_the_grade_badge_per_route_is_rendered():
    page = _page(DIRTY)
    assert "BRONZE" in page
    assert 'class="badge bronze"' in page


def test_the_page_refreshes_itself_so_nobody_has_to_touch_the_keyboard():
    assert 'http-equiv="refresh"' in _page(CLEAN)


# ---------------------------------------------- it must pass its own audit --
def test_the_security_page_passes_the_audit_it_displays():
    """A security dashboard that fails the security checks is the worst
    possible screenshot. Q1, Q2 and Q3 are checked against the real page."""
    from forge import audit
    from forge.audit import Fetched

    policy = audit.load_policy()
    body = _page(DIRTY)
    fetched = Fetched(route="/security", url="http://test/security", status=200,
                      headers={}, body=body, ok=True)

    class _Client:
        def get(self, url, headers=None):
            raise AssertionError("the DOM checks must not need the network here")

    findings = audit.check_dom(fetched, policy, _Client(), "http://test", {"/": 200, "/products": 200, "/security": 200})
    fired = {f["check_id"] for f in findings}
    assert "Q1" not in fired, "no image without alt text"
    assert "Q2" not in fired, "no external link without rel=noopener"
    assert "Q3" not in fired, "the page has a title and a meta description"


def test_the_page_leaks_no_secret_shaped_strings():
    from forge import audit
    from forge.audit import Fetched

    body = _page(DIRTY)
    fetched = Fetched(route="/security", url="http://test/security", status=200, headers={}, body=body, ok=True)
    assert audit.check_secrets(fetched, audit.load_policy()) == [], "the dashboard must not trip S10 itself"

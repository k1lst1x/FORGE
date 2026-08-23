"""
tests/test_fix_planning.py -- why fix runs used to fail VERIFY, and why they stop.

run_f1e86721 opened on "X-Frame-Options missing on /", justified itself with
"add X-Frame-Options in the security-headers middleware", then wrote
pulse/routes/security.py -- a route module, which cannot set response headers
for other routes. The header was never set, the re-audit found the finding
exactly as before, and the run was rejected three times and escalated with no
verify data recorded anywhere.

Four things had to be true for that not to happen again, and each has a section
below:

  1. the planner is told where things live, as a hard constraint
  2. S1-S6 share one middleware, so they are planned and verified together
  3. a retry can see which files the last attempt wrote and why it was rejected
  4. every attempt's verify result is on the run record and the API
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from forge import audit, planner, verify as verifier
from forge.models import AuditResult, ChangeRequest, INTAKE_FINDING, VerifyResult
from forge.planner import PlannerUnavailable

S2 = {
    "finding_id": "f_s2",
    "check_id": "S2",
    "family": "security_headers",
    "severity": "HIGH",
    "route": "/",
    "title": "X-Frame-Options or CSP frame-ancestors present",
    "evidence": "Neither X-Frame-Options nor a CSP frame-ancestors directive, observed on 200 for /",
    "suggested_fix_hint": "Same security-headers middleware; set X-Frame-Options DENY",
}
S1 = dict(S2, finding_id="f_s1", check_id="S1", title="Content-Security-Policy present",
          evidence="No Content-Security-Policy header, observed on 200 for /")
S3 = dict(S2, finding_id="f_s3", check_id="S3", severity="MED",
          title="Strict-Transport-Security present", evidence="No Strict-Transport-Security header")
S9 = {"finding_id": "f_s9", "check_id": "S9", "severity": "HIGH", "route": "/",
      "title": "Sensitive paths unreachable", "evidence": "GET /.env returned 200",
      "suggested_fix_hint": "Add a route guard"}

TRIAGE = {"classification": "AUTOFIX_SAFE", "justification": "Contained to one file."}
FILES = {"pulse/main.py": "from fastapi import FastAPI\napp = FastAPI()\n"}
FAMILY = {"name": "security_headers", "findings": [S1, S2, S3]}


# --------------------------------------------------------------- the fakes --
class FakeUsage:
    input_tokens = 5000
    output_tokens = 1800


class FakeBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class FakeResponse:
    usage = FakeUsage()

    def __init__(self, payload, stop_reason="end_turn"):
        self.content = [FakeBlock(json.dumps(payload) if isinstance(payload, dict) else payload)]
        self.stop_reason = stop_reason


class FakeMessages:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return FakeResponse(self.payloads[min(len(self.calls) - 1, len(self.payloads) - 1)])


class FakeClient:
    def __init__(self, *payloads):
        self.messages = FakeMessages(payloads)


def _reply(files, rationale="Added one security-headers middleware."):
    return {"rationale": rationale, "files": files}


TEST_FILE = {"path": "tests/test_headers.py", "content": "def test_h():\n    assert True\n",
             "reason": "regression test"}
MIDDLEWARE = {"path": "pulse/main.py", "content": "app = FastAPI()\n# middleware\n",
              "reason": "one security-headers middleware"}
ROUTE_MODULE = {"path": "pulse/routes/security.py", "content": "router = APIRouter()\n",
                "reason": "what run_f1e86721 actually wrote"}

GOOD = _reply([MIDDLEWARE, TEST_FILE])
THE_MISTAKE = _reply([ROUTE_MODULE, TEST_FILE])


def _prompt(client, index=0):
    return client.messages.calls[index]["messages"][0]["content"]


# ==========================================================================
# 1. the repo map
# ==========================================================================
def test_the_fix_prompt_carries_the_repo_map_as_a_constraint():
    client = FakeClient(GOOD)
    planner.plan_fix(S2, TRIAGE, FILES, {}, client=client, family=FAMILY)
    user = _prompt(client)
    assert "THE REPO MAP" in user
    assert "HARD CONSTRAINTS" in user


def test_the_repo_map_names_the_only_correct_file_for_a_header():
    flat = " ".join(planner.REPO_MAP.split())
    assert "THE ONLY CORRECT FILE IS pulse/main.py" in flat
    assert "NOTHING under pulse/routes/" in flat
    assert "cannot set response headers for OTHER routes" in flat


def test_the_repo_map_covers_templates_routes_and_tests():
    flat = " ".join(planner.REPO_MAP.split())
    assert "pulse/templates/<page>.html" in flat, "page content"
    assert "pulse/routes/<name>.py" in flat, "route behaviour"
    assert "tests/test_<thing>.py" in flat, "tests"


def test_a_header_fix_written_to_a_route_module_is_re_asked_then_refused():
    """The exact shape of run_f1e86721: a route module for a header finding."""
    client = FakeClient(THE_MISTAKE, THE_MISTAKE)
    with pytest.raises(PlannerUnavailable, match="outside pulse/main.py twice"):
        planner.plan_fix(S2, TRIAGE, FILES, {}, client=client, family=FAMILY)

    assert len(client.messages.calls) == 2, "exactly one re-ask, not a loop"
    reask = _prompt(client, 1)
    assert "pulse/routes/security.py" in reask, "the re-ask names the file it wrote"
    assert "cannot set response headers" in reask, "and why that file cannot work"


def test_the_re_ask_recovers_a_run_that_started_wrong():
    client = FakeClient(THE_MISTAKE, GOOD)
    changeset = planner.plan_fix(S2, TRIAGE, FILES, {}, client=client, family=FAMILY)
    assert changeset.paths == ["pulse/main.py", "tests/test_headers.py"]


def test_a_header_fix_that_forgets_main_py_is_caught_too():
    """Not touching pulse/main.py at all is the same failure, quieter."""
    only_a_test = _reply([TEST_FILE])
    client = FakeClient(only_a_test, only_a_test)
    with pytest.raises(PlannerUnavailable):
        planner.plan_fix(S2, TRIAGE, FILES, {}, client=client, family=FAMILY)
    assert "You did not touch pulse/main.py" in _prompt(client, 1)


def test_the_rail_only_applies_to_header_findings():
    """S9 is a route guard. It is allowed to live wherever it works."""
    client = FakeClient(_reply([ROUTE_MODULE, TEST_FILE]))
    changeset = planner.plan_fix(S9, TRIAGE, FILES, {}, client=client)
    assert "pulse/routes/security.py" in changeset.paths


# ==========================================================================
# 2. families -- S1-S6 are one fix
# ==========================================================================
def test_the_policy_puts_s1_to_s6_in_one_family():
    policy = audit.load_policy()
    assert policy["families"]["security_headers"] == ["S1", "S2", "S3", "S4", "S5", "S6"]
    for check_id in ("S1", "S2", "S3", "S4", "S5", "S6"):
        assert audit.family_of(check_id) == "security_headers"
    assert audit.family_of("S9") is None, "a route guard is not a header"


def test_a_finding_carries_its_family():
    policy = audit.load_policy()
    finding = audit._finding(policy, "S2", "/", "no header")
    assert finding["family"] == "security_headers"
    assert audit._finding(policy, "S9", "/", "exposed")["family"] is None


def test_the_planner_is_given_every_open_finding_in_the_family():
    client = FakeClient(GOOD)
    planner.plan_fix(S2, TRIAGE, FILES, {}, client=client, family=FAMILY)
    user = _prompt(client)
    assert "`security_headers` FAMILY" in user
    for check_id in ("S1", "S2", "S3"):
        assert check_id + " " in user
    assert "No Content-Security-Policy header" in user, "the sibling's own evidence"


def test_the_family_prompt_says_one_at_a_time_cannot_work():
    client = FakeClient(GOOD)
    planner.plan_fix(S2, TRIAGE, FILES, {}, client=client, family=FAMILY)
    flat = " ".join(_prompt(client).split())
    assert "Closing them one at a time cannot succeed" in flat
    assert "EVERY finding listed above is gone" in flat


def test_the_family_prompt_spells_out_the_one_middleware():
    client = FakeClient(GOOD)
    planner.plan_fix(S2, TRIAGE, FILES, {}, client=client, family=FAMILY)
    user = _prompt(client)
    for header in ("Content-Security-Policy", "X-Frame-Options", "Strict-Transport-Security",
                   "X-Content-Type-Options", "Referrer-Policy"):
        assert header in user
    assert "STRIP the Server header" in user
    assert "Write it once." in user


def test_a_lone_finding_gets_no_family_noise():
    client = FakeClient(_reply([{"path": "pulse/main.py", "content": "x", "reason": "guard"}, TEST_FILE]))
    planner.plan_fix(S9, TRIAGE, FILES, {}, client=client)
    user = _prompt(client)
    assert "FAMILY" not in user
    assert "Do not fix other findings" in user, "the single-finding scope survives"


# ==========================================================================
# 2b. VERIFY expects the whole family closed
# ==========================================================================
def _cr(finding, family_findings=None, attempts=0):
    cr = ChangeRequest(run_id="run_test", intake=INTAKE_FINDING, title="t", finding=finding)
    cr.attempts = attempts
    if family_findings is not None:
        cr.context["family"] = "security_headers"
        cr.context["family_findings"] = family_findings
    return cr


def _audit(findings, reachable=True):
    return AuditResult(findings=list(findings), reachable=reachable, routes_checked=["/"])


CHANGESET = [MIDDLEWARE, TEST_FILE]


@pytest.fixture
def green_tests(monkeypatch):
    monkeypatch.setattr(verifier, "run_tests", lambda changeset, cwd=None: (True, "2 passed"))


def _wire(monkeypatch, before, after):
    from contextlib import contextmanager

    @contextmanager
    def fake_serve(port=None, cwd=None):
        yield "http://127.0.0.1:9999", None

    monkeypatch.setattr(verifier, "serve_candidate", fake_serve)
    monkeypatch.setattr(verifier, "_baseline", lambda routes: before)
    monkeypatch.setattr("forge.audit.run_audit", lambda base_url=None, routes=None, **kw: after)


def test_closing_the_target_but_leaving_the_family_open_is_rejected(monkeypatch, green_tests):
    """Close S2, leave S1 and S3: the route is exactly as unshippable as before."""
    _wire(monkeypatch, before=_audit([S1, S2, S3]), after=_audit([S1, S3]))
    result = verifier.verify(CHANGESET, _cr(S2, [S1, S2, S3]))

    assert result.ok is False
    assert "family was not closed in one change" in result.evidence
    assert {f["check_id"] for f in result.findings_still_open} == {"S1", "S3"}
    assert "security_headers" in result.rejected_reason


def test_closing_the_whole_family_verifies(monkeypatch, green_tests):
    _wire(monkeypatch, before=_audit([S1, S2, S3]), after=_audit([]))
    result = verifier.verify(CHANGESET, _cr(S2, [S1, S2, S3]))

    assert result.ok is True
    assert set(result.findings_closed) == {"f_s1", "f_s2", "f_s3"}
    assert result.findings_still_open == []
    assert result.rejected_reason == ""


def test_a_finding_with_no_family_is_unaffected(monkeypatch, green_tests):
    _wire(monkeypatch, before=_audit([S9]), after=_audit([]))
    assert verifier.verify(CHANGESET, _cr(S9)).ok is True


# ==========================================================================
# 3. a retry can see why it failed
# ==========================================================================
PREVIOUS = {
    "attempt": 1,
    "paths": ["pulse/routes/security.py", "tests/test_security_frame_options.py"],
    "changeset": [dict(ROUTE_MODULE)],
    "finding_still_open": True,
    "family_still_open": ["S1", "S2", "S3"],
    "verify": {
        "tests_passed": True,
        "findings_closed": [],
        "findings_introduced": [],
        "rejected_reason": "S2 on / (HIGH) is still present after the patch",
        "evidence": "The finding this run exists to close, S2 on / (HIGH), is STILL PRESENT",
    },
}


def test_a_retry_is_told_which_files_the_last_attempt_wrote():
    client = FakeClient(GOOD)
    planner.plan_fix(S2, TRIAGE, FILES, {}, previous=PREVIOUS, attempt=2, client=client, family=FAMILY)
    user = _prompt(client)
    assert "FILES YOUR PREVIOUS ATTEMPT WROTE" in user
    assert "pulse/routes/security.py" in user
    assert "tests/test_security_frame_options.py" in user


def test_a_retry_is_told_the_finding_was_still_there():
    client = FakeClient(GOOD)
    planner.plan_fix(S2, TRIAGE, FILES, {}, previous=PREVIOUS, attempt=2, client=client, family=FAMILY)
    flat = " ".join(_prompt(client).split())
    assert "WAS THE FINDING STILL PRESENT AFTER YOUR PATCH?" in flat
    assert "YES." in flat
    assert "STILL OPEN IN THIS FAMILY AFTER YOUR LAST ATTEMPT: S1, S2, S3" in flat


def test_a_retry_is_told_the_exact_rejection_reason():
    client = FakeClient(GOOD)
    planner.plan_fix(S2, TRIAGE, FILES, {}, previous=PREVIOUS, attempt=2, client=client, family=FAMILY)
    user = _prompt(client)
    assert "rejected because:    S2 on / (HIGH) is still present after the patch" in user
    assert "STILL PRESENT" in user, "the raw evidence, not a summary"


def test_attempt_two_is_told_attempt_one_edited_the_wrong_file():
    """The whole point: attempt 2 must not repeat attempt 1's mistake."""
    client = FakeClient(GOOD)
    planner.plan_fix(S2, TRIAGE, FILES, {}, previous=PREVIOUS, attempt=2, client=client, family=FAMILY)
    flat = " ".join(_prompt(client).split())
    assert "DIAGNOSIS OF YOUR LAST ATTEMPT" in flat
    assert "you wrote pulse/routes/security.py, which is a route module" in flat
    assert "Write the middleware in pulse/main.py." in flat


def test_no_diagnosis_is_invented_when_the_file_was_right():
    innocent = dict(PREVIOUS, paths=["pulse/main.py"], changeset=[dict(MIDDLEWARE)])
    client = FakeClient(GOOD)
    planner.plan_fix(S2, TRIAGE, FILES, {}, previous=innocent, attempt=2, client=client, family=FAMILY)
    assert "DIAGNOSIS OF YOUR LAST ATTEMPT" not in _prompt(client)


# ==========================================================================
# 4. the rejection is on the run record and the API
# ==========================================================================
def test_a_verify_result_produces_one_record_per_attempt():
    result = VerifyResult(
        ok=False, tests_passed=True, attempt=2, tests_output="1 failed",
        findings_closed=["f_s2"], findings_introduced=["f_new"],
        findings_still_open=[{"check_id": "S1"}], rejected_reason="S1 is still open",
    )
    record = result.record()
    assert record == {
        "attempt": 2,
        "ok": False,
        "tests_passed": True,
        "tests_output": "1 failed",
        "findings_closed": ["f_s2"],
        "findings_introduced": ["f_new"],
        "findings_still_open": [{"check_id": "S1"}],
        "rejected_reason": "S1 is still open",
    }


def test_the_run_summary_publishes_the_whole_verify_history():
    cr = _cr(S2)
    cr.verify_attempts = [
        VerifyResult(ok=False, tests_passed=True, attempt=1, rejected_reason="wrong file").record(),
        VerifyResult(ok=True, tests_passed=True, attempt=2).record(),
    ]
    published = cr.summary()["verify"]
    assert [entry["attempt"] for entry in published] == [1, 2]
    assert published[0]["rejected_reason"] == "wrong file"
    assert published[1]["ok"] is True


def test_the_engine_appends_one_entry_per_attempt(monkeypatch):
    from forge import engine

    cr = _cr(S2, attempts=1)
    monkeypatch.setattr(
        engine.verify_mod, "verify",
        lambda changeset, request: VerifyResult(
            ok=False, tests_passed=True, attempt=2, rejected_reason="still open"),
    )
    monkeypatch.setattr(engine, "_upsert", lambda request: None)

    engine.verify(cr)
    engine.verify(cr)

    assert [entry["attempt"] for entry in cr.verify_attempts] == [2, 2]
    assert all(entry["rejected_reason"] == "still open" for entry in cr.verify_attempts)
    assert cr.summary()["verify"] == cr.verify_attempts


def test_the_api_serves_the_verify_history(monkeypatch):
    from fastapi.testclient import TestClient

    # forge.api resolves to the app/api/ PACKAGE, which shadowed the
    # forge-control module. That module is now app/control.py.
    from forge import control as api

    record = _cr(S2).summary()
    record["verify"] = [
        {"attempt": 1, "ok": False, "rejected_reason": "wrote pulse/routes/security.py"},
        {"attempt": 2, "ok": True, "rejected_reason": ""},
    ]
    monkeypatch.setattr(api.store, "get_run", lambda run_id: record if run_id == "run_test" else None)

    with TestClient(api.app) as client:
        payload = client.get("/api/runs/run_test").json()

    assert [entry["attempt"] for entry in payload["verify"]] == [1, 2]
    assert payload["verify"][0]["rejected_reason"] == "wrote pulse/routes/security.py"


def test_verify_records_the_reason_a_test_failure_rejected_the_run(monkeypatch):
    monkeypatch.setattr(verifier, "run_tests", lambda changeset, cwd=None: (False, "E assert 404 == 200"))
    result = verifier.verify(CHANGESET, _cr(S2, attempts=2))

    assert result.attempt == 3
    assert result.tests_output == "E assert 404 == 200"
    assert result.rejected_reason == "the tests that accompany this change did not pass"
    assert result.record()["attempt"] == 3


# ==========================================================================
# the plumbing both checks depend on
# ==========================================================================
def test_both_checks_run_against_the_tree_the_patch_was_written_into():
    """pytest and the candidate app must see the changeset, which only exists
    in the factory worktree. Run either from the main checkout and every
    attempt fails for a reason that has nothing to do with the patch."""
    from forge import vcs

    if not vcs.WORKTREE.exists():
        pytest.skip("no factory worktree on this machine")
    assert verifier.candidate_cwd() == str(vcs.WORKTREE)


def test_verify_falls_back_loudly_when_there_is_no_worktree(monkeypatch, tmp_path, caplog):
    from forge import vcs

    monkeypatch.setattr(vcs, "WORKTREE", tmp_path / "gone", raising=False)
    with caplog.at_level("WARNING"):
        assert verifier.candidate_cwd() == __import__("os").getcwd()
    assert "does NOT contain the patch" in caplog.text


# ==========================================================================
# 5. the planner has to be shown the file it is expected to rewrite
# ==========================================================================
def test_the_route_file_is_found_by_its_decorator_not_by_substring():
    """`"/" in body` matches every file in the repo.

    That is not a near miss -- it is why run_f1e86721 was handed
    pulse/routes/security.py and asked to fix a response header on "/", which
    is a question that file cannot answer. The planner did not choose the wrong
    file; it was never shown the right one.
    """
    from forge import engine

    assert engine._route_file("/") == Path("pulse") / "main.py"
    assert engine._route_file("/products") == Path("pulse") / "main.py"
    assert engine._route_file("/security") == Path("pulse") / "routes" / "security.py"


def test_serves_route_ignores_a_bare_slash_in_a_comment_or_an_import():
    from forge import engine

    assert engine._serves_route('@app.get("/", response_class=HTMLResponse)', "/")
    assert engine._serves_route("@router.get('/security')", "/security")
    assert not engine._serves_route('import os  # see docs/notes for "/"', "/")
    assert not engine._serves_route('httpx.get("http://x/")', "/")


def test_the_planner_is_always_given_main_py():
    """Headers, middleware, CORS and docs guards live there whatever the route."""
    from forge import engine

    files = engine._context_files({"route": "/security"})
    assert "pulse/main.py" in files
    assert "pulse/routes/security.py" in files
    assert "app = FastAPI" in files["pulse/main.py"], "the real content, not a stub"


def test_context_file_keys_use_forward_slashes():
    """The planner keys every rail off this spelling."""
    from forge import engine

    for key in engine._context_files({"route": "/"}):
        assert chr(92) not in key, "a Windows-style key makes the shrink rail miss"
        assert key.startswith("pulse/")


def test_a_patch_that_deletes_most_of_the_file_is_refused():
    """The rail that should have caught the run which replaced the whole app."""
    original = "\n".join("line %d" % i for i in range(90))
    stub = _reply([
        {"path": "pulse/main.py", "content": "app = FastAPI()\n", "reason": "middleware"},
        TEST_FILE,
    ])
    client = FakeClient(stub)
    with pytest.raises(PlannerUnavailable, match="shrinks it from 90 lines"):
        planner.plan_fix(S2, TRIAGE, {"pulse/main.py": original}, {}, client=client, family=FAMILY)


def test_the_rail_fires_on_a_windows_style_key_too():
    """The exact miss: the engine handed a backslash key, the rail saw nothing."""
    original = "\n".join("line %d" % i for i in range(90))
    stub = _reply([
        {"path": "pulse/main.py", "content": "app = FastAPI()\n", "reason": "middleware"},
        TEST_FILE,
    ])
    client = FakeClient(stub)
    with pytest.raises(PlannerUnavailable, match="shrinks it from 90 lines"):
        planner.plan_fix(S2, TRIAGE, {"pulse" + chr(92) + "main.py": original}, {},
                         client=client, family=FAMILY)

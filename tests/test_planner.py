"""
tests/test_planner.py -- the code-writing agent.

What matters here is not that the model writes good code -- that is what VERIFY
is for. What matters is that the planner cannot produce a changeset that is
unsafe to apply: no path outside pulse/ and tests/, no partial file, no silent
loss of the test file, and a retry that actually knows why the last one failed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from forge import planner
from forge.planner import PlannerUnavailable

FINDING = {
    "check_id": "S12",
    "severity": "MED",
    "route": "/products",
    "title": "API documentation endpoint not exposed in production mode",
    "evidence": "GET /docs returned 200 with an OpenAPI schema listing 11 endpoints",
    "suggested_fix_hint": "Guard the docs route behind settings.ENV == dev",
}
TRIAGE = {"classification": "AUTOFIX_SAFE", "justification": "Contained to one file."}
FILES = {"pulse/main.py": "from fastapi import FastAPI\napp = FastAPI()\n"}
TEMPLATES = {"base.html": "<!doctype html><html><head><title>Pulse</title></head></html>"}


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
    def __init__(self, payloads, stop_reason="end_turn"):
        self.payloads = list(payloads)
        self.stop_reason = stop_reason
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        payload = self.payloads[min(len(self.calls) - 1, len(self.payloads) - 1)]
        return FakeResponse(payload, self.stop_reason)


class FakeClient:
    def __init__(self, *payloads, stop_reason="end_turn"):
        self.messages = FakeMessages(payloads, stop_reason)


def _reply(files, rationale="Guarded the docs route behind the environment setting."):
    return {"rationale": rationale, "files": files}


GOOD = _reply([
    {"path": "pulse/main.py", "content": "app = FastAPI(docs_url=None)\n", "reason": "close the docs endpoint"},
    {"path": "tests/test_docs_closed.py", "content": "def test_docs():\n    assert True\n", "reason": "regression test"},
])


# ------------------------------------------------------------ blast radius --
def test_a_path_outside_pulse_and_tests_is_refused_not_written():
    """The factory can change the app it built. It cannot change itself."""
    client = FakeClient(_reply([
        {"path": "forge/engine.py", "content": "# owned", "reason": "disable the gate"},
        {"path": "pulse/main.py", "content": "app = FastAPI(docs_url=None)\n", "reason": "the actual fix"},
        {"path": "tests/test_x.py", "content": "def test_x():\n    assert True\n", "reason": "test"},
    ]))
    changeset = planner.plan_fix(FINDING, TRIAGE, FILES, {}, client=client)
    assert "forge/engine.py" not in changeset.paths
    assert changeset.rejected_paths == ["forge/engine.py"]
    assert set(changeset.paths) == {"pulse/main.py", "tests/test_x.py"}


def test_path_traversal_is_refused():
    client = FakeClient(_reply([
        {"path": "pulse/../forge/vcs.py", "content": "x", "reason": "escape"},
        {"path": "tests/test_x.py", "content": "def test_x():\n    assert True\n", "reason": "test"},
    ]))
    changeset = planner.plan_fix(FINDING, TRIAGE, FILES, {}, client=client)
    assert changeset.paths == ["tests/test_x.py"]


def test_a_changeset_with_nothing_writable_is_refused_entirely():
    client = FakeClient(_reply([{"path": "forge/engine.py", "content": "x", "reason": "no"}]))
    with pytest.raises(PlannerUnavailable, match="outside pulse/ and tests/"):
        planner.plan_fix(FINDING, TRIAGE, FILES, {}, client=client)


# ------------------------------------------------------- always with a test --
def test_a_missing_test_file_earns_exactly_one_re_ask():
    without = _reply([{"path": "pulse/main.py", "content": "x", "reason": "fix"}])
    client = FakeClient(without, GOOD)
    changeset = planner.plan_fix(FINDING, TRIAGE, FILES, {}, client=client)
    assert changeset.test_included is True
    assert len(client.messages.calls) == 2, "one re-ask, not a loop"
    assert "no file under tests/" in client.messages.calls[1]["messages"][0]["content"]
    assert changeset.tokens_in == 10000, "both calls are counted"


def test_a_persistently_missing_test_is_reported_not_hidden():
    without = _reply([{"path": "pulse/main.py", "content": "x", "reason": "fix"}])
    client = FakeClient(without, without)
    changeset = planner.plan_fix(FINDING, TRIAGE, FILES, {}, client=client)
    assert changeset.test_included is False, "VERIFY must be able to see this"
    assert len(client.messages.calls) == 2


# --------------------------------------------------------- partial content --
def test_a_truncated_file_is_never_shipped():
    """Hitting the token ceiling mid-file means the content would corrupt the
    file it replaces. Refusing beats writing half a module."""
    client = FakeClient(GOOD, stop_reason="max_tokens")
    with pytest.raises(PlannerUnavailable, match="truncated"):
        planner.plan_fix(FINDING, TRIAGE, FILES, {}, client=client)


def test_no_credentials_escalates_rather_than_inventing_a_patch(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    with pytest.raises(PlannerUnavailable, match="No ANTHROPIC_API_KEY"):
        planner.plan_fix(FINDING, TRIAGE, FILES, {})


# ------------------------------------------------------- the prompt shape --
def test_both_entry_points_share_one_prompt_shape():
    fix_client, feature_client = FakeClient(GOOD), FakeClient(GOOD)
    planner.plan_fix(FINDING, TRIAGE, FILES, {}, client=fix_client)
    planner.plan_feature("Add an out-of-stock page.", FILES, TEMPLATES, {}, client=feature_client)
    for client in (fix_client, feature_client):
        call = client.messages.calls[0]
        assert call["model"] == planner.MODEL
        assert call["system"] == planner.SYSTEM_PROMPT
        assert call["output_config"]["format"]["type"] == "json_schema"


def test_the_system_prompt_forbids_diffs_and_demands_a_test():
    prompt = planner.SYSTEM_PROMPT
    assert "never a diff" in prompt
    assert "rest of file unchanged" in prompt, "the elision failure mode is named explicitly"
    assert "ALWAYS include a test file" in prompt
    assert "pulse/" in prompt and "tests/" in prompt
    assert "minimal change" in prompt


def test_plan_fix_gets_the_evidence_the_file_and_the_hint():
    client = FakeClient(GOOD)
    planner.plan_fix(FINDING, TRIAGE, FILES, {}, client=client)
    user = client.messages.calls[0]["messages"][0]["content"]
    assert "GET /docs returned 200" in user, "the evidence"
    assert "app = FastAPI()" in user, "the current content of the file that serves the route"
    assert "Guard the docs route" in user, "the fix hint"
    assert "AUTOFIX_SAFE" in user, "what triage decided, so it acts within that"
    assert "MINIMAL change" in user


def test_plan_fix_tells_it_not_to_fix_things_it_was_not_asked_to():
    client = FakeClient(GOOD)
    planner.plan_fix(FINDING, TRIAGE, FILES, {}, client=client)
    user = client.messages.calls[0]["messages"][0]["content"]
    assert "Do not fix other findings" in user


def test_plan_feature_carries_the_policy_as_acceptance_criteria():
    """The same seventeen checks that will audit the page in five minutes."""
    client = FakeClient(GOOD)
    planner.plan_feature("Add a page showing out-of-stock products.", FILES, TEMPLATES, {}, client=client)
    user = client.messages.calls[0]["messages"][0]["content"]
    assert "ACCEPTANCE CRITERIA FOR YOUR CODE, NOT ADVICE" in user
    for check_id in ("S1", "S9", "S10", "Q1", "Q2", "Q3", "Q4", "P1"):
        assert f"[{check_id} " in user, f"{check_id} must be in the acceptance criteria"
    for demanded in ("Content-Security-Policy", "alt attribute", 'rel="noopener', "meta name=\"description\""):
        assert demanded in user


def test_plan_feature_shows_the_existing_style_and_the_base_template():
    client = FakeClient(GOOD)
    planner.plan_feature("Add a page.", FILES, TEMPLATES, {}, client=client)
    user = client.messages.calls[0]["messages"][0]["content"]
    assert "from fastapi import FastAPI" in user, "existing routes as a style example"
    assert "<!doctype html>" in user, "the base template to extend"


# ------------------------------------------------------------- the retry --
def test_a_retry_carries_the_previous_attempt_and_the_exact_failure():
    """Strictly more information than the first call, not just 'try again'."""
    previous = {
        "attempt": 1,
        "changeset": [{"path": "pulse/main.py", "content": "app = FastAPI()  # forgot the guard", "reason": "first try"}],
        "verify": {
            "tests_passed": False,
            "findings_closed": [],
            "findings_introduced": ["f_new1"],
            "evidence": "tests/test_docs_closed.py::test_docs FAILED - assert 404 == 200",
        },
    }
    client = FakeClient(GOOD)
    planner.plan_fix(FINDING, TRIAGE, FILES, {}, previous=previous, attempt=2, client=client)
    user = client.messages.calls[0]["messages"][0]["content"]
    assert "THIS IS ATTEMPT 2" in user
    assert "forgot the guard" in user, "what it produced last time"
    assert "assert 404 == 200" in user, "the exact verify output, not a summary"
    assert "f_new1" in user, "the finding it introduced"
    flat = " ".join(user.split())  # the prompt wraps; the meaning should not depend on that
    assert "closed one hole and opened another" in flat


def test_a_first_attempt_carries_no_retry_noise():
    client = FakeClient(GOOD)
    planner.plan_fix(FINDING, TRIAGE, FILES, {}, client=client)
    assert "THIS IS ATTEMPT" not in client.messages.calls[0]["messages"][0]["content"]


def test_the_changeset_carries_what_the_span_needs():
    client = FakeClient(GOOD)
    changeset = planner.plan_fix(FINDING, TRIAGE, FILES, {}, attempt=3, client=client)
    assert isinstance(changeset, list), "every existing call site treats this as a list"
    assert changeset.rationale.startswith("Guarded the docs route")
    assert changeset.tokens_in == 5000 and changeset.tokens_out == 1800
    assert changeset.attempt == 3
    assert changeset.model == planner.MODEL

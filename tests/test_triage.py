"""
tests/test_triage.py -- all five classifications, plus the rails.

The two paths the plan says must be reliable rather than lucky -- UPSTREAM_OUTAGE
and FALSE_POSITIVE -- are tested hardest. The outage path takes no model call at
all, so it is tested as the deterministic fact it is.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from forge import triage
from forge.models import (
    AUTOFIX_SAFE,
    DUPLICATE,
    FALSE_POSITIVE,
    NEEDS_HUMAN_DESIGN,
    NEW_FEATURE,
    UPSTREAM_OUTAGE,
)

PAGE = "<html><head><title>Products</title></head><body><h1>Pulse</h1></body></html>"

FINDING = {
    "finding_id": "f_7a3c",
    "check_id": "S9",
    "severity": "HIGH",
    "route": "/stock-alerts",
    "title": "Sensitive paths unreachable",
    "evidence": "GET /admin returned 200 (text/html, 711 bytes)",
    "suggested_fix_hint": "Add a route guard",
    "occurrences": 3,
}


def _finding(**over):
    return dict(FINDING, **over)


class FakeUsage:
    input_tokens = 1234
    output_tokens = 210


class FakeBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class FakeResponse:
    stop_reason = "end_turn"
    usage = FakeUsage()

    def __init__(self, payload):
        self.content = [FakeBlock(json.dumps(payload) if isinstance(payload, dict) else payload)]


class FakeMessages:
    def __init__(self, payload, explode=None):
        self.payload, self.explode, self.calls = payload, explode, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.explode:
            raise self.explode
        return FakeResponse(self.payload)


class FakeClient:
    def __init__(self, payload=None, explode=None):
        self.messages = FakeMessages(payload, explode)


def _reply(classification, should_act, justification="A specific and defensible reason, stated at length.", confidence=0.9, blast="contained"):
    return {
        "classification": classification,
        "should_act": should_act,
        "justification": justification,
        "confidence": confidence,
        "blast_radius": blast,
    }


# ------------------------------------------------- the deterministic guards --
def test_upstream_outage_is_decided_without_a_model_call():
    """Mode 4. It must be right every time, not usually."""
    client = FakeClient(_reply(AUTOFIX_SAFE, True))
    result = triage.classify(_finding(reachable=False), "", {}, [], client=client)
    assert result.classification == UPSTREAM_OUTAGE
    assert result.should_act is False
    assert result.decided_by == "guard"
    assert result.confidence == 1.0
    assert client.messages.calls == [], "an outage must not cost a model call"


def test_empty_page_source_is_an_outage_even_without_the_reachable_flag():
    result = triage.classify(_finding(), "   ", {}, [])
    assert result.classification == UPSTREAM_OUTAGE
    assert result.decided_by == "guard"


def test_outage_justification_names_what_was_observed():
    result = triage.classify(_finding(reachable=False), "", {}, [])
    assert "/stock-alerts" in result.justification
    assert "nothing there to check" in result.justification


def test_a_reachable_page_is_never_an_outage():
    """A broken app and an absent one look identical in the metrics. Content is
    the only thing that separates them, so content must decide it."""
    client = FakeClient(_reply(AUTOFIX_SAFE, True))
    result = triage.classify(_finding(), PAGE, {}, [], client=client)
    assert result.classification != UPSTREAM_OUTAGE
    assert len(client.messages.calls) == 1


def test_duplicate_is_a_lookup_not_an_opinion():
    history = [{"check_id": "S9", "route": "/stock-alerts", "status": "in_flight", "run_id": "run_abc123"}]
    client = FakeClient(_reply(AUTOFIX_SAFE, True))
    result = triage.classify(_finding(), PAGE, {}, history, client=client)
    assert result.classification == DUPLICATE
    assert result.should_act is False
    assert "run_abc123" in result.justification
    assert client.messages.calls == []


def test_a_closed_prior_finding_is_not_a_duplicate():
    history = [{"check_id": "S9", "route": "/stock-alerts", "status": "closed", "run_id": "run_old"}]
    client = FakeClient(_reply(AUTOFIX_SAFE, True))
    result = triage.classify(_finding(), PAGE, {}, history, client=client)
    assert result.classification == AUTOFIX_SAFE


# ------------------------------------------------------- the model decisions --
def test_autofix_safe_acts():
    client = FakeClient(_reply(AUTOFIX_SAFE, True))
    result = triage.classify(_finding(), PAGE, {}, [], client=client)
    assert result.classification == AUTOFIX_SAFE
    assert result.should_act is True
    assert result.decided_by == "model"
    assert result.tokens_in == 1234 and result.tokens_out == 210
    assert result.model == triage.MODEL


def test_needs_human_design_declines():
    client = FakeClient(_reply(NEEDS_HUMAN_DESIGN, False, "Changing the CORS allowlist could break a client we cannot see.", blast="clients"))
    result = triage.classify(_finding(check_id="S3"), PAGE, {}, [], client=client)
    assert result.classification == NEEDS_HUMAN_DESIGN
    assert result.should_act is False
    assert result.blast_radius == "clients"


def test_false_positive_declines_and_keeps_its_reason():
    reason = "The matched string is a Subresource Integrity hash, a public content digest, not a credential."
    client = FakeClient(_reply(FALSE_POSITIVE, False, reason))
    result = triage.classify(_finding(check_id="S10"), PAGE, {}, [], client=client)
    assert result.classification == FALSE_POSITIVE
    assert result.should_act is False
    assert "Subresource Integrity" in result.justification


def test_a_brief_is_classified_as_new_feature_through_the_same_call():
    client = FakeClient(_reply(NEW_FEATURE, True, "Coherent, in scope, one route and one template."))
    brief = {"check_id": "BRIEF", "severity": "NONE", "route": None, "title": "Out of stock page", "evidence": "Add a page showing out-of-stock products."}
    result = triage.classify(brief, "", {}, [], client=client)
    assert result.classification == NEW_FEATURE
    assert result.should_act is True
    # A brief with no page source must NOT trip the outage guard.
    assert result.decided_by == "model"


# ------------------------------------------------------------------- rails --
def test_the_policy_can_veto_an_autofix_but_the_model_cannot_veto_the_policy():
    """S8 is marked escalate. Even a confident AUTOFIX_SAFE must not patch it."""
    client = FakeClient(_reply(AUTOFIX_SAFE, True, "Looks like a one-line CORS change.", confidence=0.99))
    result = triage.classify(_finding(check_id="S8"), PAGE, {}, [], client=client)
    assert result.classification == NEEDS_HUMAN_DESIGN
    assert result.should_act is False
    assert "escalate-only" in result.justification


def test_a_self_contradicting_answer_resolves_towards_declining():
    client = FakeClient(_reply(AUTOFIX_SAFE, False))
    result = triage.classify(_finding(), PAGE, {}, [], client=client)
    assert result.should_act is False
    assert "safer reading" in result.justification


def test_a_false_positive_without_a_reason_is_not_dismissable():
    client = FakeClient(_reply(FALSE_POSITIVE, False, "nope"))
    result = triage.classify(_finding(check_id="S10"), PAGE, {}, [], client=client)
    assert result.classification == NEEDS_HUMAN_DESIGN
    assert "defensible written reason" in result.justification


def test_an_unreachable_model_declines_rather_than_guessing():
    client = FakeClient(explode=RuntimeError("connection reset"))
    result = triage.classify(_finding(), PAGE, {}, [], client=client)
    assert result.classification == NEEDS_HUMAN_DESIGN
    assert result.should_act is False
    assert result.decided_by == "fallback"
    assert "connection reset" in result.justification


def test_markdown_fences_would_still_parse():
    client = FakeClient("```json\n" + json.dumps(_reply(AUTOFIX_SAFE, True)) + "\n```")
    assert triage.classify(_finding(), PAGE, {}, [], client=client).classification == AUTOFIX_SAFE


# ------------------------------------------------------- the request shape --
def test_the_call_is_shaped_the_way_the_plan_specifies():
    client = FakeClient(_reply(AUTOFIX_SAFE, True))
    triage.classify(_finding(), PAGE, {}, [], client=client)
    call = client.messages.calls[0]
    # Was pinned to the literal "claude-sonnet-4-6". The model per role is now
    # configuration (and differs per provider), so what is actually invariant is
    # that the call uses the configured triage model -- not which one that is.
    assert call["model"] == triage.MODEL
    assert call["max_tokens"] == 1000
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["output_config"]["format"]["schema"]["properties"]["classification"]["enum"] == triage.CLASSIFICATIONS


def test_the_system_prompt_carries_the_required_emphases():
    prompt = triage.SYSTEM_PROMPT
    assert "Return JSON only" in prompt
    assert "no markdown fences" in prompt
    assert "fixes everything it sees is dangerous" in prompt
    assert "Declining is a valid" in prompt
    assert "a human will read it" in prompt
    assert "whether the page returned real content at all" in prompt
    for name in (AUTOFIX_SAFE, NEEDS_HUMAN_DESIGN, FALSE_POSITIVE, UPSTREAM_OUTAGE, DUPLICATE):
        assert name in prompt


def test_the_user_message_gives_the_model_what_it_needs_to_decide():
    client = FakeClient(_reply(AUTOFIX_SAFE, True))
    triage.classify(_finding(), PAGE, {"pulse/main.py": "app = FastAPI()"}, [], client=client)
    user = client.messages.calls[0]["messages"][0]["content"]
    assert "GET /admin returned 200" in user, "the evidence"
    assert "Pulse" in user, "what the page actually served"
    assert "app = FastAPI()" in user, "the file that serves the route"
    assert "occurrences: 3" in user, "how many times it has been seen"


def test_no_credentials_falls_back_to_policy_heuristics_and_says_so(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    result = triage.classify(_finding(), PAGE, {}, [])
    assert result.decided_by == "heuristic"
    assert "without model triage" in result.justification
    escalating = triage.classify(_finding(check_id="S8"), PAGE, {}, [])
    assert escalating.should_act is False, "the policy still holds without credentials"

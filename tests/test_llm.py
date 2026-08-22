"""
tests/test_llm.py -- the provider abstraction.

The interesting failures here are silent ones: tokens counted differently by
each provider (mis-billing), and a truncated file that looks like a successful
call (a corrupted patch). Both are pinned below.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from forge import llm, planner, triage
from forge.llm import BudgetExceeded


@pytest.fixture(autouse=True)
def clean_budget(monkeypatch):
    monkeypatch.delenv("FORGE_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("FORGE_BUDGET_USD", raising=False)
    monkeypatch.delenv("FORGE_TOKEN_HEADROOM", raising=False)
    llm.reset_budget()
    yield
    llm.reset_budget()


# --------------------------------------------------------------- fakes ----
class AnthropicUsage:
    def __init__(self, inp, out, cached=0):
        self.input_tokens, self.output_tokens, self.cache_read_input_tokens = inp, out, cached


class Block:
    type = "text"

    def __init__(self, text):
        self.text = text


class AnthropicResponse:
    def __init__(self, text="{}", inp=1000, out=200, cached=0, stop_reason="end_turn"):
        self.content = [Block(text)]
        self.usage = AnthropicUsage(inp, out, cached)
        self.stop_reason = stop_reason


class FakeAnthropic:
    def __init__(self, response):
        self._response = response
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


class PromptDetails:
    def __init__(self, cached):
        self.cached_tokens = cached


class CompletionDetails:
    def __init__(self, reasoning):
        self.reasoning_tokens = reasoning


class OpenAIUsage:
    def __init__(self, prompt, completion, cached=0, reasoning=0):
        self.prompt_tokens, self.completion_tokens = prompt, completion
        self.prompt_tokens_details = PromptDetails(cached)
        self.completion_tokens_details = CompletionDetails(reasoning)


class OpenAIMessage:
    def __init__(self, content):
        self.content = content


class OpenAIChoice:
    def __init__(self, content, finish_reason):
        self.message = OpenAIMessage(content)
        self.finish_reason = finish_reason


class OpenAIResponse:
    def __init__(self, text="{}", prompt=1000, completion=200, cached=0, reasoning=0, finish_reason="stop"):
        self.choices = [OpenAIChoice(text, finish_reason)]
        self.usage = OpenAIUsage(prompt, completion, cached, reasoning)


class FakeOpenAI:
    def __init__(self, response):
        self._response = response
        self.calls = []
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


# ---------------------------------------------------- provider selection ----
def test_provider_defaults_to_anthropic():
    assert llm.provider() == "anthropic"


def test_provider_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("FORGE_LLM_PROVIDER", "openai")
    assert llm.provider() == "openai"
    monkeypatch.setenv("FORGE_LLM_PROVIDER", "ANTHROPIC")
    assert llm.provider() == "anthropic"


def test_an_unknown_provider_fails_loudly(monkeypatch):
    monkeypatch.setenv("FORGE_LLM_PROVIDER", "llama")
    with pytest.raises(ValueError, match="must be 'anthropic' or 'openai'"):
        llm.provider()


def test_models_differ_per_role_and_per_provider(monkeypatch):
    monkeypatch.delenv("FORGE_TRIAGE_MODEL", raising=False)
    monkeypatch.delenv("FORGE_PLANNER_MODEL", raising=False)
    assert llm.model_for("triage", "FORGE_TRIAGE_MODEL") == "claude-haiku-4-5-20251001"
    assert llm.model_for("planner", "FORGE_PLANNER_MODEL") == "claude-sonnet-5"
    monkeypatch.setenv("FORGE_LLM_PROVIDER", "openai")
    assert llm.model_for("triage", "FORGE_TRIAGE_MODEL") == "gpt-5.6-luna"
    assert llm.model_for("planner", "FORGE_PLANNER_MODEL") == "gpt-5.6-terra"


def test_an_explicit_model_env_var_wins(monkeypatch):
    monkeypatch.setenv("FORGE_PLANNER_MODEL", "gpt-5.6-sol")
    assert llm.model_for("planner", "FORGE_PLANNER_MODEL") == "gpt-5.6-sol"


def test_startup_names_the_key_for_the_selected_provider_only(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="No ANTHROPIC_API_KEY"):
        llm.require_credentials()
    monkeypatch.setenv("FORGE_LLM_PROVIDER", "openai")
    with pytest.raises(RuntimeError, match="No OPENAI_API_KEY"):
        llm.require_credentials()
    # Holding one provider's key must not require the other's.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    llm.require_credentials()


# ------------------------------------------------- token normalisation ----
def test_anthropic_tokens_normalise():
    client = FakeAnthropic(AnthropicResponse(inp=900, out=200, cached=4000))
    result = llm.generate("sys", "user", 1000, "claude-sonnet-5", client=client)
    assert result.input_tokens == 900, "Anthropic reports uncached input directly"
    assert result.cached_input_tokens == 4000
    assert result.output_tokens == 200
    assert result.reasoning_tokens == 0
    assert result.provider == "anthropic"


def test_openai_tokens_normalise_the_other_direction(monkeypatch):
    """OpenAI's prompt_tokens INCLUDES cached tokens. Anthropic's does not.
    Counting them the same way would over-bill every cached OpenAI call."""
    monkeypatch.setenv("FORGE_LLM_PROVIDER", "openai")
    client = FakeOpenAI(OpenAIResponse(prompt=4900, completion=1500, cached=4000, reasoning=1200))
    result = llm.generate("sys", "user", 1000, "gpt-5.6-terra", client=client)
    assert result.input_tokens == 900, "cached tokens are subtracted out of prompt_tokens"
    assert result.cached_input_tokens == 4000
    assert result.output_tokens == 1500, "completion_tokens already includes reasoning"
    assert result.reasoning_tokens == 1200
    assert result.provider == "openai"


def test_the_same_spend_costs_the_same_on_both_providers_after_normalisation(monkeypatch):
    anthropic_result = llm.generate("sys", "user", 1000, "claude-sonnet-5",
                                    client=FakeAnthropic(AnthropicResponse(inp=900, out=200, cached=4000)))
    llm.reset_budget()
    monkeypatch.setenv("FORGE_LLM_PROVIDER", "openai")
    openai_result = llm.generate("sys", "user", 1000, "gpt-5.6-terra",
                                 client=FakeOpenAI(OpenAIResponse(prompt=4900, completion=200, cached=4000)))
    assert anthropic_result.input_tokens == openai_result.input_tokens
    assert anthropic_result.cached_input_tokens == openai_result.cached_input_tokens


# ------------------------------------------------------ cost arithmetic ----
def test_cost_counts_cached_input_at_a_tenth():
    # sonnet-5 is 2/10 per million. 1M uncached in = $2. 1M cached in = $0.20.
    assert llm.cost_usd("claude-sonnet-5", 1_000_000, 0) == pytest.approx(2.0)
    assert llm.cost_usd("claude-sonnet-5", 0, 0, 1_000_000) == pytest.approx(0.20)
    assert llm.cost_usd("claude-sonnet-5", 1_000_000, 0, 1_000_000) == pytest.approx(2.20)


def test_cost_bills_reasoning_as_output_without_double_counting(monkeypatch):
    """Reasoning tokens are billed as output -- and OpenAI's completion_tokens
    already contains them. Adding them again would bill thinking twice."""
    monkeypatch.setenv("FORGE_LLM_PROVIDER", "openai")
    client = FakeOpenAI(OpenAIResponse(prompt=0, completion=1_000_000, reasoning=800_000))
    result = llm.generate("sys", "user", 1000, "gpt-5.6-terra", client=client)
    # terra is 2/12. 1M output (of which 800k is thinking) = $12, not $21.60.
    assert result.cost_usd == pytest.approx(12.0)


def test_every_model_in_the_rate_table_prices():
    for model, (rate_in, rate_out) in llm.RATES.items():
        assert llm.cost_usd(model, 1_000_000, 0) == pytest.approx(rate_in)
        assert llm.cost_usd(model, 0, 1_000_000) == pytest.approx(rate_out)


def test_an_unknown_model_does_not_crash_the_run():
    assert llm.cost_usd("some-model-we-have-not-priced", 1_000_000, 1_000_000) == 0.0


# ------------------------------------------------------------- budget ----
def test_spend_accumulates_and_is_reported(monkeypatch):
    client = FakeAnthropic(AnthropicResponse(inp=1_000_000, out=0))
    llm.generate("sys", "user", 1000, "claude-sonnet-5", client=client)
    llm.generate("sys", "user", 1000, "claude-sonnet-5", client=client)
    status = llm.budget_status()
    assert status["spend_usd"] == pytest.approx(4.0)
    assert status["calls_by_model"] == {"claude-sonnet-5": 2}
    assert status["budget_usd"] == 25.0


def test_budget_raises_rather_than_crashing(monkeypatch):
    monkeypatch.setenv("FORGE_BUDGET_USD", "3")
    client = FakeAnthropic(AnthropicResponse(inp=1_000_000, out=0))  # $2 a call
    llm.generate("sys", "user", 1000, "claude-sonnet-5", client=client)   # 2.0, under
    llm.generate("sys", "user", 1000, "claude-sonnet-5", client=client)   # 4.0, over
    with pytest.raises(BudgetExceeded, match=r"budget of \$3.00 is spent"):
        llm.generate("sys", "user", 1000, "claude-sonnet-5", client=client)


def test_the_budget_is_checked_before_spending_not_after(monkeypatch):
    monkeypatch.setenv("FORGE_BUDGET_USD", "0.0001")
    client = FakeAnthropic(AnthropicResponse(inp=1_000_000, out=0))
    llm.generate("sys", "user", 1000, "claude-sonnet-5", client=client)
    with pytest.raises(BudgetExceeded):
        llm.generate("sys", "user", 1000, "claude-sonnet-5", client=client)
    assert client.calls and len(client.calls) == 1, "no call is made once the budget is gone"


def test_a_spent_budget_escalates_the_run_instead_of_erroring(monkeypatch):
    """BudgetExceeded rides the PlannerUnavailable path in the engine."""
    monkeypatch.setenv("FORGE_BUDGET_USD", "0.0001")
    llm.generate("sys", "user", 1000, "claude-sonnet-5",
                 client=FakeAnthropic(AnthropicResponse(inp=1_000_000, out=0)))

    from forge import engine
    from forge.models import AuditResult, TriageResult, AUTOFIX_SAFE

    monkeypatch.setattr("forge.engine.audit_mod.run_audit",
                        lambda base_url=None, routes=None, **kw: AuditResult())
    monkeypatch.setattr("forge.engine.triage_mod.classify",
                        lambda *a, **k: TriageResult(AUTOFIX_SAFE, True, "Contained.", 0.9, "contained"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    cr = engine.run_from_finding({"finding_id": "f_b", "check_id": "S12", "severity": "MED",
                                  "route": "/", "title": "docs open", "evidence": "GET /docs returned 200",
                                  "suggested_fix_hint": "guard it", "page_source": "<html>Pulse</html>"})
    assert cr.outcome == "escalated", "a spent budget must never crash a run"
    assert cr.status == "escalated"
    assert cr.changeset == [], "nothing is written when the budget is gone"
    assert "budget" in cr.context["escalation_reason"].lower()


# --------------------------------------------------- the truncation rail ----
# A file cut off mid-write corrupts the file it replaces. The planner refuses
# on truncation, and that rail must fire identically on both providers.
FIX_ARGS = (
    {"check_id": "S12", "severity": "MED", "route": "/products", "title": "docs open",
     "evidence": "GET /docs returned 200", "suggested_fix_hint": "guard it"},
    {"classification": "AUTOFIX_SAFE", "justification": "Contained."},
    {"pulse/main.py": "app = FastAPI()"},
    {},
)

WHOLE_FILE = '{"rationale": "Guarded the docs route.", "files": [{"path": "pulse/main.py", "content": "app = FastAPI(docs_url=None)", "reason": "fix"}, {"path": "tests/test_d.py", "content": "def test_d(): assert True", "reason": "test"}]}'


def test_anthropic_truncation_is_refused():
    """stop_reason max_tokens."""
    client = FakeAnthropic(AnthropicResponse(text=WHOLE_FILE, stop_reason="max_tokens"))
    with pytest.raises(planner.PlannerUnavailable, match="truncated"):
        planner.plan_fix(*FIX_ARGS, client=client)


def test_openai_truncation_is_refused(monkeypatch):
    """finish_reason length."""
    monkeypatch.setenv("FORGE_LLM_PROVIDER", "openai")
    client = FakeOpenAI(OpenAIResponse(text=WHOLE_FILE, finish_reason="length"))
    with pytest.raises(planner.PlannerUnavailable, match="truncated"):
        planner.plan_fix(*FIX_ARGS, client=client)


def test_openai_empty_completion_after_reasoning_is_refused(monkeypatch):
    """The worst failure mode: reasoning consumed the whole budget, the call
    looks successful, and nothing came back. It must not read as success."""
    monkeypatch.setenv("FORGE_LLM_PROVIDER", "openai")
    client = FakeOpenAI(OpenAIResponse(text="", completion=8000, reasoning=8000, finish_reason="length"))
    with pytest.raises(planner.PlannerUnavailable, match="truncated"):
        planner.plan_fix(*FIX_ARGS, client=client)


def test_the_refusal_message_names_the_reasoning_cost(monkeypatch):
    monkeypatch.setenv("FORGE_LLM_PROVIDER", "openai")
    client = FakeOpenAI(OpenAIResponse(text="", completion=8000, reasoning=8000, finish_reason="length"))
    with pytest.raises(planner.PlannerUnavailable) as caught:
        planner.plan_fix(*FIX_ARGS, client=client)
    assert "8000 of 8000 output tokens were reasoning" in str(caught.value)


def test_an_untruncated_empty_response_is_also_refused(monkeypatch):
    """Empty content with a clean finish still must not overwrite a file."""
    monkeypatch.setenv("FORGE_LLM_PROVIDER", "openai")
    client = FakeOpenAI(OpenAIResponse(text="", finish_reason="stop"))
    with pytest.raises(planner.PlannerUnavailable, match="no content"):
        planner.plan_fix(*FIX_ARGS, client=client)


# ------------------------------------------- reasoning-token mitigations ----
def test_the_planner_asks_for_low_reasoning_effort_on_openai(monkeypatch):
    monkeypatch.setenv("FORGE_LLM_PROVIDER", "openai")
    client = FakeOpenAI(OpenAIResponse(text=WHOLE_FILE))
    planner.plan_fix(*FIX_ARGS, client=client)
    assert client.calls[0]["reasoning_effort"] == "low"


def test_reasoning_effort_is_overridable(monkeypatch):
    monkeypatch.setenv("FORGE_LLM_PROVIDER", "openai")
    monkeypatch.setenv("FORGE_OPENAI_REASONING_EFFORT", "none")
    client = FakeOpenAI(OpenAIResponse(text=WHOLE_FILE))
    planner.plan_fix(*FIX_ARGS, client=client)
    assert client.calls[0]["reasoning_effort"] == "none"


def test_openai_gets_token_headroom_and_anthropic_does_not(monkeypatch):
    anthropic_client = FakeAnthropic(AnthropicResponse(text=WHOLE_FILE))
    planner.plan_fix(*FIX_ARGS, client=anthropic_client)
    assert anthropic_client.calls[0]["max_tokens"] == planner.MAX_TOKENS

    monkeypatch.setenv("FORGE_LLM_PROVIDER", "openai")
    monkeypatch.setenv("FORGE_TOKEN_HEADROOM", "1.6")
    openai_client = FakeOpenAI(OpenAIResponse(text=WHOLE_FILE))
    planner.plan_fix(*FIX_ARGS, client=openai_client)
    asked = openai_client.calls[0]["max_completion_tokens"]
    assert asked > planner.MAX_TOKENS, "reasoning needs room of its own"


def test_headroom_is_clamped_to_the_model_ceiling(monkeypatch):
    monkeypatch.setenv("FORGE_LLM_PROVIDER", "openai")
    monkeypatch.setenv("FORGE_TOKEN_HEADROOM", "100")
    assert llm._headroom(16000, "gpt-5.6-terra", "openai") == llm.MAX_OUTPUT_TOKENS["gpt-5.6-terra"]


# ------------------------------ the provider is an implementation detail ----
TRIAGE_REPLY = '{"classification": "AUTOFIX_SAFE", "should_act": true, "justification": "Contained to the one file that serves this route.", "confidence": 0.9, "blast_radius": "contained"}'


def test_switching_provider_changes_nothing_the_planner_returns(monkeypatch):
    on_anthropic = planner.plan_fix(*FIX_ARGS, client=FakeAnthropic(AnthropicResponse(text=WHOLE_FILE)))
    monkeypatch.setenv("FORGE_LLM_PROVIDER", "openai")
    on_openai = planner.plan_fix(*FIX_ARGS, client=FakeOpenAI(OpenAIResponse(text=WHOLE_FILE)))

    assert list(on_anthropic) == list(on_openai), "same files, same content, same reasons"
    assert on_anthropic.paths == on_openai.paths
    assert on_anthropic.rationale == on_openai.rationale
    assert on_anthropic.test_included == on_openai.test_included
    assert type(on_anthropic) is type(on_openai)


def test_switching_provider_changes_nothing_triage_returns(monkeypatch):
    finding = {"check_id": "S12", "severity": "MED", "route": "/products", "title": "docs open",
               "evidence": "GET /docs returned 200", "suggested_fix_hint": "guard it"}
    page = "<html><body>Pulse</body></html>"

    on_anthropic = triage.classify(finding, page, {}, [], client=FakeAnthropic(AnthropicResponse(text=TRIAGE_REPLY)))
    monkeypatch.setenv("FORGE_LLM_PROVIDER", "openai")
    on_openai = triage.classify(finding, page, {}, [], client=FakeOpenAI(OpenAIResponse(text=TRIAGE_REPLY)))

    assert on_anthropic.classification == on_openai.classification
    assert on_anthropic.should_act == on_openai.should_act
    assert on_anthropic.justification == on_openai.justification
    assert on_anthropic.confidence == on_openai.confidence
    assert on_anthropic.decided_by == on_openai.decided_by == "model"


def test_the_guards_do_not_care_which_provider_is_configured(monkeypatch):
    """A deterministic fact stays deterministic. No call, either way."""
    outage = {"check_id": "S1", "severity": "HIGH", "route": "/products", "title": "no CSP",
              "evidence": "Connection refused", "reachable": False}
    for which in ("anthropic", "openai"):
        monkeypatch.setenv("FORGE_LLM_PROVIDER", which)
        result = triage.classify(outage, "", {}, [])
        assert result.classification == "UPSTREAM_OUTAGE"
        assert result.decided_by == "guard"


def test_each_backend_sends_the_system_prompt_the_way_its_api_wants(monkeypatch):
    """Anthropic takes a system parameter; Chat Completions has no such thing
    and needs it as the first message. Same prompt, two wire shapes."""
    anthropic_client = FakeAnthropic(AnthropicResponse(text=WHOLE_FILE))
    planner.plan_fix(*FIX_ARGS, client=anthropic_client)
    call = anthropic_client.calls[0]
    assert call["system"] == planner.SYSTEM_PROMPT
    assert call["cache_control"] == {"type": "ephemeral"}, "~2k identical tokens every call"
    assert call["messages"][0]["role"] == "user"

    monkeypatch.setenv("FORGE_LLM_PROVIDER", "openai")
    openai_client = FakeOpenAI(OpenAIResponse(text=WHOLE_FILE))
    planner.plan_fix(*FIX_ARGS, client=openai_client)
    call = openai_client.calls[0]
    assert "system" not in call
    assert call["messages"][0] == {"role": "system", "content": planner.SYSTEM_PROMPT}
    assert call["messages"][1]["role"] == "user"


def test_triage_asks_for_json_mode_on_openai(monkeypatch):
    monkeypatch.setenv("FORGE_LLM_PROVIDER", "openai")
    client = FakeOpenAI(OpenAIResponse(text=TRIAGE_REPLY))
    triage.classify({"check_id": "S12", "route": "/", "evidence": "x"}, "<html>p</html>", {}, [], client=client)
    assert client.calls[0]["response_format"] == {"type": "json_object"}

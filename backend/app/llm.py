"""
forge/llm.py -- one interface, two providers.

    generate(system, user, max_tokens, model, json_mode=False, json_schema=None)
        -> LLMResult

Everything the factory says to a model goes through here. triage.py and
planner.py do not import an SDK and do not know which provider is configured;
they ask for text and get back a normalised result.

WHAT "NORMALISED" MEANS, PRECISELY
--------------------------------------------------------------------------
The two providers count tokens differently, and getting this wrong silently
mis-bills every run:

  input_tokens   UNCACHED input only, both providers. Anthropic reports this
                 natively; OpenAI's prompt_tokens INCLUDES cached tokens, so
                 the cached portion is subtracted out here.
  output_tokens  Total billed output. On OpenAI, completion_tokens already
                 includes the hidden reasoning tokens, so cost is computed from
                 this number alone -- adding reasoning again would double-bill.
  reasoning_tokens  The hidden subset, reported for visibility, NOT added to
                 cost on top of output_tokens.

REASONING TOKENS ARE A TRAP FOR CODE GENERATION
--------------------------------------------------------------------------
GPT-5.6 models emit hidden thinking billed as output and not returned. It
consumes the same max_tokens budget the file content has to fit inside. If
thinking eats the budget you get a truncated file -- or worse, an empty one
with finish_reason "length", which looks like a successful call and returns
nothing. Both are treated as truncation here, and the planner refuses to ship
a truncated file because it would corrupt the file it replaces.

Mitigations: reasoning effort defaults to "low" for code generation (we want
file content, not deliberation, and a human gates the output anyway), and the
caller's max_tokens is multiplied by FORGE_TOKEN_HEADROOM so thinking has room
of its own.

OWNER: ROHIT.
"""
from __future__ import annotations

import collections
import logging
import os
import threading
import time
from dataclasses import dataclass, field

log = logging.getLogger("forge.llm")

ANTHROPIC, OPENAI = "anthropic", "openai"

#: Default model per role per provider. Roles differ so a cheap model can do
#: classification while a stronger one writes code.
DEFAULT_MODELS = {
    ("triage", ANTHROPIC): "claude-haiku-4-5-20251001",
    ("triage", OPENAI): "gpt-5.6-luna",
    ("planner", ANTHROPIC): "claude-sonnet-5",
    ("planner", OPENAI): "gpt-5.6-terra",
}

#: USD per million tokens, (input, output).
RATES = {
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "gpt-5.6-sol": (5.0, 30.0),
    "gpt-5.6-terra": (2.0, 12.0),
    "gpt-5.6-luna": (0.20, 1.20),
}

#: Cached input reads bill at 10% of the base input rate.
CACHED_INPUT_MULTIPLIER = 0.10

#: Conservative output ceilings used only to clamp headroom expansion. These
#: are floors on our own ambition, not published limits -- override per model
#: with FORGE_MAX_OUTPUT_<MODEL> if a model can take more.
MAX_OUTPUT_TOKENS = {
    "claude-opus-5": 128000,
    "claude-sonnet-5": 128000,
    "claude-haiku-4-5": 64000,
    "gpt-5.6-sol": 32000,
    "gpt-5.6-terra": 32000,
    "gpt-5.6-luna": 32000,
}
DEFAULT_MAX_OUTPUT = 32000

ENV_KEY = {ANTHROPIC: "ANTHROPIC_API_KEY", OPENAI: "OPENAI_API_KEY"}


class BudgetExceeded(RuntimeError):
    """The run would spend past FORGE_BUDGET_USD.

    Raised before the call is made, never during one. The engine turns this
    into a clean escalation on the same path as PlannerUnavailable, and triage
    declines on it -- it must never crash a run.
    """


@dataclass
class LLMResult:
    text: str = ""
    input_tokens: int = 0  # uncached only
    output_tokens: int = 0  # total billed output, reasoning included
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0
    truncated: bool = False
    model: str = ""
    provider: str = ""
    latency_ms: float = 0.0
    cost_usd: float = 0.0
    finish_reason: str | None = None

    @property
    def empty(self) -> bool:
        return not (self.text or "").strip()


# --------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------
def _api_key_names(which: str) -> tuple[str, ...]:
    return {
        ANTHROPIC: ("ANTHROPIC_API_KEY", "FORGE_ANTHROPIC_API_KEY"),
        OPENAI: ("OPENAI_API_KEY", "FORGE_OPENAI_API_KEY"),
    }[which]


def _api_key_for(which: str) -> str:
    for key in _api_key_names(which):
        value = os.getenv(key)
        if value and value.strip():
            return value.strip()
    return ""


def provider() -> str:
    chosen = (os.getenv("FORGE_LLM_PROVIDER") or ANTHROPIC).strip().lower()
    if chosen not in (ANTHROPIC, OPENAI):
        raise ValueError(f"FORGE_LLM_PROVIDER must be 'anthropic' or 'openai', got {chosen!r}")

    if credentials_available(chosen):
        return chosen

    fallback = OPENAI if chosen == ANTHROPIC else ANTHROPIC
    if credentials_available(fallback):
        log.warning(
            "Preferred provider %s has no configured API key; falling back to %s.",
            chosen,
            fallback,
        )
        return fallback

    return chosen


def model_for(role: str, override_env: str | None = None) -> str:
    """The model for a role, honouring an explicit env override."""
    if override_env:
        explicit = os.getenv(override_env)
        if explicit:
            return explicit.strip()
    return DEFAULT_MODELS[(role, provider())]


def credentials_available(which: str | None = None) -> bool:
    which = which or provider()
    return bool(_api_key_for(which))


def require_credentials(which: str | None = None) -> None:
    """Fail loudly, naming the key for the SELECTED provider only.

    Requiring both keys would mean nobody can run on one provider without
    holding an account on the other.
    """
    which = which or provider()
    if not credentials_available(which):
        key_names = " / ".join(_api_key_names(which))
        raise RuntimeError(
            f"No {ENV_KEY[which]} is configured, and FORGE_LLM_PROVIDER is {which!r}. "
            f"Export {key_names}, or switch provider with FORGE_LLM_PROVIDER."
        )


def _rate_key(model: str) -> str:
    """Match a model id to its rate row, tolerating date suffixes."""
    if model in RATES:
        return model
    for known in RATES:
        if model.startswith(known):
            return known
    return ""


def cost_usd(model: str, input_tokens: int, output_tokens: int, cached_input_tokens: int = 0) -> float:
    """Cost of one call.

    output_tokens is the provider's billed output and already includes any
    reasoning tokens, so reasoning is not added again here.
    """
    key = _rate_key(model)
    if not key:
        log.warning("no rate table entry for model %r -- its spend is not being counted", model)
        return 0.0
    rate_in, rate_out = RATES[key]
    million = 1_000_000
    return (
        (input_tokens / million) * rate_in
        + (cached_input_tokens / million) * rate_in * CACHED_INPUT_MULTIPLIER
        + (output_tokens / million) * rate_out
    )


# --------------------------------------------------------------------------
# the budget ledger
# --------------------------------------------------------------------------
_LOCK = threading.Lock()
_SPEND_USD = 0.0
_CALLS = collections.Counter()
_WARNED = False


def budget_usd() -> float:
    try:
        return float(os.getenv("FORGE_BUDGET_USD", "25"))
    except ValueError:
        return 25.0


def budget_status() -> dict:
    """What GET /api/status serves."""
    with _LOCK:
        return {
            "spend_usd": round(_SPEND_USD, 4),
            "budget_usd": budget_usd(),
            "calls_by_model": dict(_CALLS),
            "provider": provider(),
        }


def reset_budget() -> None:
    global _SPEND_USD, _WARNED
    with _LOCK:
        _SPEND_USD = 0.0
        _WARNED = False
        _CALLS.clear()


def _check_budget() -> None:
    """Checked before the call, so we never spend past the ceiling."""
    limit = budget_usd()
    with _LOCK:
        spent = _SPEND_USD
    if spent >= limit:
        raise BudgetExceeded(
            f"The run budget of ${limit:.2f} is spent (${spent:.4f} used). No further model "
            "calls will be made. Raise FORGE_BUDGET_USD to continue."
        )


def _record(model: str, amount: float) -> None:
    global _SPEND_USD, _WARNED
    limit = budget_usd()
    should_warn = False
    with _LOCK:
        _SPEND_USD += amount
        _CALLS[model] += 1
        spent = _SPEND_USD
        if spent >= limit * 0.8 and not _WARNED:
            _WARNED = True
            should_warn = True
    if should_warn:
        log.warning(
            "LLM spend is at $%.4f of the $%.2f budget (%.0f%%)", spent, limit, 100 * spent / max(limit, 1e-9)
        )


# --------------------------------------------------------------------------
# backends
# --------------------------------------------------------------------------
def _anthropic_call(system, user, max_tokens, model, json_mode, json_schema, client):
    if client is None:
        require_credentials(ANTHROPIC)
        import anthropic

        client = anthropic.Anthropic()

    kwargs = {
        "model": model,
        "max_tokens": max_tokens,
        # The system prompt is ~2k identical tokens on every call. Top-level
        # auto-caching marks the last cacheable block, which is exactly this
        # one, and cache reads bill at 10% of base input.
        "cache_control": {"type": "ephemeral"},
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    if json_schema:
        kwargs["output_config"] = {"format": {"type": "json_schema", "schema": json_schema}}

    response = client.messages.create(**kwargs)
    usage = getattr(response, "usage", None)
    stop_reason = getattr(response, "stop_reason", None)
    text = next((b.text for b in response.content if getattr(b, "type", None) == "text"), "")
    return LLMResult(
        text=text,
        input_tokens=getattr(usage, "input_tokens", 0) or 0,
        output_tokens=getattr(usage, "output_tokens", 0) or 0,
        cached_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        reasoning_tokens=0,
        truncated=stop_reason == "max_tokens",
        finish_reason=stop_reason,
    )


def _openai_reasoning_effort() -> str:
    return (os.getenv("FORGE_OPENAI_REASONING_EFFORT") or "low").strip().lower()


def _openai_call(system, user, max_tokens, model, json_mode, json_schema, client):
    if client is None:
        require_credentials(OPENAI)
        import openai

        client = openai.OpenAI()

    kwargs = {
        "model": model,
        # Chat Completions has no separate system parameter -- the system
        # prompt is the first message.
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "max_completion_tokens": max_tokens,
    }
    if json_mode or json_schema:
        # The word JSON appears in both of our system prompts, which json_object
        # mode requires.
        kwargs["response_format"] = {"type": "json_object"}

    effort = _openai_reasoning_effort()
    if effort and effort != "default":
        kwargs["reasoning_effort"] = effort

    try:
        response = client.chat.completions.create(**kwargs)
    except TypeError as exc:
        # An SDK or model that does not take reasoning_effort. Retry once
        # without it rather than losing the call.
        if "reasoning_effort" not in str(exc):
            raise
        log.warning("model %s rejected reasoning_effort=%s, retrying without it", model, effort)
        kwargs.pop("reasoning_effort", None)
        response = client.chat.completions.create(**kwargs)

    choice = response.choices[0]
    text = getattr(choice.message, "content", None) or ""
    finish = getattr(choice, "finish_reason", None)

    usage = getattr(response, "usage", None)
    prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
    completion_tokens = getattr(usage, "completion_tokens", 0) or 0
    cached = 0
    reasoning = 0
    details = getattr(usage, "prompt_tokens_details", None)
    if details is not None:
        cached = getattr(details, "cached_tokens", 0) or 0
    details = getattr(usage, "completion_tokens_details", None)
    if details is not None:
        reasoning = getattr(details, "reasoning_tokens", 0) or 0

    # An empty completion with finish_reason "length" means reasoning consumed
    # the whole budget. It looks like success and returns nothing, so it is
    # treated as what it is: a truncated response.
    truncated = finish == "length" or (finish == "length" and not text.strip())
    if not text.strip() and finish == "length":
        log.error("model %s returned an empty completion after reasoning -- the budget was consumed by thinking", model)
        truncated = True

    return LLMResult(
        text=text,
        input_tokens=max(prompt_tokens - cached, 0),  # OpenAI's prompt_tokens includes cached
        output_tokens=completion_tokens,  # already includes reasoning_tokens
        cached_input_tokens=cached,
        reasoning_tokens=reasoning,
        truncated=truncated,
        finish_reason=finish,
    )


# --------------------------------------------------------------------------
# the one interface
# --------------------------------------------------------------------------
def _headroom(max_tokens: int, model: str, which: str) -> int:
    """Give hidden reasoning room of its own on OpenAI reasoning models.

    Without this the file content and the thinking compete for one budget, and
    the thinking wins.
    """
    if which != OPENAI:
        return max_tokens
    try:
        factor = float(os.getenv("FORGE_TOKEN_HEADROOM", "1.6"))
    except ValueError:
        factor = 1.6
    wanted = int(max_tokens * factor)
    ceiling = MAX_OUTPUT_TOKENS.get(_rate_key(model) or model, DEFAULT_MAX_OUTPUT)
    if wanted > ceiling:
        log.warning(
            "token headroom wanted %s for %s but the ceiling is %s -- clamping, so reasoning "
            "may still crowd out the response",
            wanted,
            model,
            ceiling,
        )
        return ceiling
    return wanted


def _generate_for_provider(
    which: str,
    system: str,
    user: str,
    max_tokens: int,
    model: str,
    json_mode: bool,
    json_schema: dict | None,
    client=None,
) -> LLMResult:
    backend = _anthropic_call if which == ANTHROPIC else _openai_call
    budgeted = _headroom(max_tokens, model, which)

    started = time.perf_counter()
    result = backend(system, user, budgeted, model, json_mode, json_schema, client)
    result.latency_ms = round((time.perf_counter() - started) * 1000, 1)
    result.model = model
    result.provider = which
    result.cost_usd = cost_usd(model, result.input_tokens, result.output_tokens, result.cached_input_tokens)
    _record(model, result.cost_usd)

    if result.truncated:
        log.warning(
            "%s/%s returned a truncated response (finish=%s, %s output tokens of which %s were "
            "reasoning)",
            which,
            model,
            result.finish_reason,
            result.output_tokens,
            result.reasoning_tokens,
        )
    return result


def _fallback_error(exc: Exception) -> bool:
    message = str(exc).lower()
    markers = (
        "credit balance",
        "insufficient credits",
        "billing",
        "quota",
        "over quota",
        "rate limit",
        "429",
        "payment required",
        "invalid api key",
        "unauthorized",
        "forbidden",
        "not authorized",
        "api error",
        "bad request",
    )
    return any(marker in message for marker in markers)


def generate(
    system: str,
    user: str,
    max_tokens: int,
    model: str,
    json_mode: bool = False,
    *,
    json_schema: dict | None = None,
    client=None,
) -> LLMResult:
    """One call to whichever provider is configured.

    json_schema is an extension beyond json_mode: on Anthropic it becomes a
    real schema constraint, which is what keeps triage's classification inside
    its enum and the planner's changeset in shape. OpenAI gets json_object
    mode, and the callers' own validation covers the rest.
    """
    # BEFORE the early return, not only inside the fallback loop below. An
    # injected client skipped the loop entirely and with it the only budget
    # check, so `generate(..., client=...)` could spend without limit -- which
    # is the one thing _check_budget exists to prevent. triage.py and
    # planner.py both pass client=, so the hole was on the live path.
    _check_budget()

    if client is not None:
        return _generate_for_provider(provider(), system, user, max_tokens, model, json_mode, json_schema, client)

    preferred = provider()
    candidates = [preferred]
    fallback = OPENAI if preferred == ANTHROPIC else ANTHROPIC
    if credentials_available(fallback):
        candidates.append(fallback)

    last_error: Exception | None = None
    for which in candidates:
        try:
            _check_budget()
            return _generate_for_provider(which, system, user, max_tokens, model, json_mode, json_schema, None)
        except Exception as exc:  # pragma: no cover - exercised by runtime fallback, not small unit tests
            last_error = exc
            if which == candidates[-1] or not _fallback_error(exc):
                raise
            log.warning(
                "LLM provider %s failed (%s); retrying with %s instead.",
                which,
                exc,
                fallback,
            )

    if last_error is not None:
        raise last_error
    raise RuntimeError("No available LLM provider could satisfy the request.")


def annotate_span(span, result: LLMResult) -> None:
    """Put the provider-neutral facts on whichever span the caller opened."""
    if span is None or result is None:
        return
    attrs = {
        "llm.provider": result.provider,
        "llm.model": result.model,
        "llm.input_tokens": result.input_tokens,
        "llm.output_tokens": result.output_tokens,
        "llm.cached_input_tokens": result.cached_input_tokens,
        "llm.reasoning_tokens": result.reasoning_tokens,
        "llm.cost_usd": round(result.cost_usd, 6),
        "llm.latency_ms": result.latency_ms,
        "llm.truncated": result.truncated,
        "llm.finish_reason": str(result.finish_reason),
    }
    for key, value in attrs.items():
        try:
            span.set_attribute(key, value)
        except Exception:
            pass

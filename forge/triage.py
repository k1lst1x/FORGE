"""
forge/triage.py -- the five classifications. The headline feature.

Every other self-healing project shows something break and something get fixed.
That is a loop, and a loop is not judgement. This module is where FORGE decides
whether to act at all, and it is allowed to say no.

    AUTOFIX_SAFE        contained, one file, low blast radius   -> act
    NEEDS_HUMAN_DESIGN  real, but the fix has consequences      -> decline
    FALSE_POSITIVE      the check fired but is wrong here       -> decline
    UPSTREAM_OUTAGE     nothing was there to check              -> decline
    DUPLICATE           same root cause already in flight       -> attach
    NEW_FEATURE         a brief, not a defect (Loop A)          -> act

HOW THE DECISION IS MADE, and why it is not all model
--------------------------------------------------------------------------
Two of these are facts, not judgements, and the plan is explicit that they have
to be reliable rather than lucky. So they are decided in code, before any API
call, and the run records which path decided:

  * UPSTREAM_OUTAGE -- "the only way to tell them apart is whether the page
    returned real content at all" is a boolean we already hold. Asking a model
    to re-derive it from prose would be strictly less reliable.
  * DUPLICATE -- whether a fix for this check on this route is already in
    flight is a lookup against history, not an opinion.

Everything that genuinely needs judgement -- is this contained enough to patch,
is this "secret" actually a public hash, would this change break a client we
cannot see -- goes to the model. The model can still return UPSTREAM_OUTAGE for
the harder case the guard cannot see: a page that returns 200 with real content
that happens to be a maintenance page.

Every TriageResult carries `decided_by` so a judge can ask "which of these did
the model decide?" and get a straight answer off the span.

OWNER: ROHIT.
"""
from __future__ import annotations

import json
import logging
import os
import time

from forge import llm
from forge.models import (
    AUTOFIX_SAFE,
    DUPLICATE,
    FALSE_POSITIVE,
    NEEDS_HUMAN_DESIGN,
    NEW_FEATURE,
    UPSTREAM_OUTAGE,
    TriageResult,
)

log = logging.getLogger("forge.triage")

MODEL = llm.model_for("triage", "FORGE_TRIAGE_MODEL")
MAX_TOKENS = int(os.getenv("FORGE_TRIAGE_MAX_TOKENS", "1000"))
MAX_EXCERPT = int(os.getenv("FORGE_TRIAGE_MAX_EXCERPT", "4000"))

CLASSIFICATIONS = [AUTOFIX_SAFE, NEEDS_HUMAN_DESIGN, FALSE_POSITIVE, UPSTREAM_OUTAGE, DUPLICATE, NEW_FEATURE]
ACTING = {AUTOFIX_SAFE, NEW_FEATURE}

#: The model cannot return a classification outside this set, or malformed JSON.
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "classification": {"type": "string", "enum": CLASSIFICATIONS},
        "should_act": {"type": "boolean"},
        "justification": {"type": "string", "minLength": 20},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "blast_radius": {"type": "string", "enum": ["contained", "service", "clients", "unknown"]},
    },
    "required": ["classification", "should_act", "justification", "confidence", "blast_radius"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You are the triage stage of an automated software factory. \
The factory audits the web app it built, and you decide what to do about each finding.

Return JSON only. No preamble, no explanation outside the JSON, no markdown fences.

Classify the finding as exactly one of:

AUTOFIX_SAFE -- contained, well understood, fixable in one file. The audit policy names \
the fix and it is mechanical. should_act: true. This is the NORMAL answer for the checks \
in this policy. Concretely, treat these as AUTOFIX_SAFE unless you can name a specific \
reason otherwise: adding security response headers, environment-guarding or disabling a \
docs endpoint, adding a route guard for /.env or /admin, setting cookie flags, stripping a \
version-bearing header, adding alt text, adding rel=noopener, adding a title or meta \
description, fixing a broken internal link.

NEEDS_HUMAN_DESIGN -- a real problem where the SAFE DIRECTION IS GENUINELY AMBIGUOUS and \
you would be guessing at intent: CORS origin allowlists, authentication and authorization, \
session handling, data retention -- anything where two reasonable engineers would pick \
different fixes. should_act: false. The bar is a CONCRETE, NAMEABLE dependency or a \
genuinely ambiguous direction, NOT the theoretical possibility that something somewhere \
relies on a misconfiguration. "An external consumer might depend on this" is not \
enough unless you can say who and why from the evidence in front of you -- that reasoning \
applied to every reachable endpoint declines every finding, and a factory that fixes \
nothing is not safer, just broken.

FALSE_POSITIVE -- the check fired but is wrong in this context. The classic case is a \
string that pattern-matches a credential but is a Subresource Integrity hash, a public \
test key, or a content digest. should_act: false, and the justification is REQUIRED.

UPSTREAM_OUTAGE -- the audit target was unreachable or served an error or maintenance \
page, so the check "failed" because nothing real was there to check. should_act: false.

DUPLICATE -- the same root cause as a fix already in flight. Attach to that run rather \
than starting a new one. should_act: false.

NEW_FEATURE -- this is a change request, not a defect. Only for briefs. Judge whether \
the brief is coherent and in scope for a small web app; if it is, should_act: true.

An automated system that fixes everything it sees is dangerous. Declining is a valid \
and often correct answer, and a confident wrong patch costs far more than a decline. \
When you decline, the justification must be specific and defensible: it is written into \
the service catalog and a human will read it and judge you by it. Name the actual reason \
this specific finding on this specific route should not be auto-patched. Never write a \
generic justification.

UPSTREAM_OUTAGE and a genuinely broken app look identical in the metrics -- both produce \
lots of failed checks. The only way to tell them apart is whether the page returned real \
content at all. If real content came back, the app is up and the finding is about the app; \
do not classify it as an outage.

DATA FINDINGS (D1 stale feed, D2 contract failure) HAVE THEIR OWN RULE. \

A stale or empty scrape caused by an unreachable or erroring SOURCE SITE is \
UPSTREAM_OUTAGE -- should_act false. Do not patch application code because a \
third-party site is down; there is nothing in our repository to fix, and a patch \
written against someone else's outage is worse than no patch. Only classify a \
data finding AUTOFIX_SAFE if the evidence shows OUR OWN pipeline is at fault -- \
the scheduler not running the scrape, a collector whose selectors no longer match \
the page, a contract that no longer describes the feed we ask for.

WHAT HAPPENS TO YOUR DECISION -- this changes the risk calculus, so weigh it.

You are not deploying anything. A patch you mark AUTOFIX_SAFE is written, its tests are \
run, the app is re-audited to confirm the finding is closed and that no new HIGH finding \
appeared, and then a HUMAN READS THE DIFF AND APPROVES IT before it merges. Nothing you \
approve reaches production unreviewed. So the cost of proposing a contained fix is one \
person glancing at a small diff; the cost of declining is that a real security hole stays \
open and a person has to do the work by hand.

Decline when the fix genuinely needs a design decision. Do not decline merely because a \
change has consequences -- every change does.

confidence is your own calibration, 0 to 1."""


# --------------------------------------------------------------------------
# prompt construction
# --------------------------------------------------------------------------
def _excerpt(text: str | None, limit: int = MAX_EXCERPT) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text or "(empty)"
    return text[:limit] + f"\n... [truncated, {len(text)} chars total]"


def _policy_action(check_id: str | None) -> str | None:
    """What the policy says about this check. A strong prior, not a gag order."""
    if not check_id:
        return None
    try:
        from forge import audit

        spec = audit.load_policy()["by_id"].get(check_id)
        return spec.get("action") if spec else None
    except Exception:
        return None


def _history_summary(history: list | None) -> str:
    rows = list(history or [])[:8]
    if not rows:
        return "No prior findings recorded for this route."
    lines = []
    for row in rows:
        lines.append(
            f"- {row.get('check_id')} on {row.get('route')} "
            f"status={row.get('status', 'open')} occurrences={row.get('occurrences', 1)} "
            f"run={row.get('run_id', 'n/a')}"
        )
    return "\n".join(lines)


def _finding_prompt(finding: dict, page_source: str, file_context, history) -> str:
    action = _policy_action(finding.get("check_id"))
    prior = ""
    if action == "escalate":
        prior = (
            "\nThe audit policy marks this check as one that should be escalated rather than "
            "auto-patched. You may disagree, but say why explicitly if you do.\n"
        )
    files = file_context if isinstance(file_context, dict) else {}
    rendered_files = "\n\n".join(
        f"--- {path} ---\n{_excerpt(content, 3000)}" for path, content in list(files.items())[:3]
    ) or "(no source file located for this route)"

    return f"""A scheduled audit of an app this factory built produced this finding.

FINDING
  check_id:   {finding.get('check_id')}
  severity:   {finding.get('severity')}
  route:      {finding.get('route')}
  title:      {finding.get('title')}
  occurrences: {finding.get('occurrences', 1)} (times seen across runs)
  evidence:   {finding.get('evidence')}
  policy fix hint: {finding.get('suggested_fix_hint')}
{prior}
WHAT THE PAGE ACTUALLY SERVED (this is how you tell an outage from a defect)
{_excerpt(page_source)}

THE SOURCE FILE THAT SERVES THIS ROUTE
{rendered_files}

PRIOR FINDINGS AND FIX ATTEMPTS ON THIS ROUTE
{_history_summary(history)}

Decide what the factory should do. Return JSON only."""


def _brief_prompt(finding: dict, file_context) -> str:
    files = file_context if isinstance(file_context, dict) else {}
    existing = ", ".join(list(files)[:10]) or "(no route files yet)"
    return f"""A person submitted this change request to the factory through the control plane.

BRIEF
  title: {finding.get('title')}
  text:  {_excerpt(finding.get('evidence'), 2000)}

EXISTING ROUTE FILES IN THE APP
{existing}

This is a request for new work, not a defect. Judge whether the brief is coherent and in
scope: it should be satisfiable by one route, one template and one test against a small
web app that displays scraped product data. If it is, classify NEW_FEATURE with
should_act true. If it is incoherent, contradictory, or far too large for one change,
classify NEEDS_HUMAN_DESIGN with should_act false and say precisely what is unclear.

Return JSON only."""


# --------------------------------------------------------------------------
# the model call
# --------------------------------------------------------------------------
def _credentials_available() -> bool:
    return llm.credentials_available()


def _call_model(system: str, user: str, client=None) -> tuple[dict, dict]:
    """One model call, through the provider abstraction.

    json_schema means the classification cannot come back outside its enum on
    Anthropic; on OpenAI it becomes json_object mode and _reconcile catches the
    rest. The system prompt still says JSON only either way.
    """
    from forge import telemetry

    with telemetry.stage_span("forge.triage.model_call", "triage") as span:
        result = llm.generate(
            system=system,
            user=user,
            max_tokens=MAX_TOKENS,
            model=MODEL,
            json_mode=True,
            json_schema=RESPONSE_SCHEMA,
            client=client,
        )
        llm.annotate_span(span, result)
        if span is not None:
            span.set_attribute("triage.model", result.model)
            span.set_attribute("triage.tokens_in", result.input_tokens)
            span.set_attribute("triage.tokens_out", result.output_tokens)
            span.set_attribute("triage.latency_ms", result.latency_ms)
            span.set_attribute("triage.stop_reason", str(result.finish_reason))

    usage = {
        "tokens_in": result.input_tokens,
        "tokens_out": result.output_tokens,
        "stop_reason": result.finish_reason,
        "latency_ms": result.latency_ms,
    }
    return json.loads(_strip_fences(result.text)), usage


def _strip_fences(text: str) -> str:
    """The schema makes this unnecessary. It costs three lines and removes a
    whole class of 15:00 failure, so it stays."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        text = text.rsplit("```", 1)[0]
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if start != -1 and end > start else text


def _reconcile(data: dict, usage: dict, check_id: str | None) -> TriageResult:
    """Turn the model's answer into a result, refusing to let it contradict itself.

    Two rails, both of which fail towards declining:
      * should_act is derived from the classification. If the model said
        AUTOFIX_SAFE with should_act false, the decline wins.
      * a check the policy marks escalate can never be auto-patched, whatever
        the model thinks. The policy can veto a patch; the model cannot veto
        the policy.
    """
    classification = data.get("classification")
    justification = (data.get("justification") or "").strip()
    implied = classification in ACTING
    stated = bool(data.get("should_act"))
    should_act = implied and stated
    notes = []

    if implied != stated:
        notes.append(f"model returned {classification} with should_act={stated}; taking the safer reading")

    if should_act and _policy_action(check_id) == "escalate":
        classification = NEEDS_HUMAN_DESIGN
        should_act = False
        notes.append(
            f"the audit policy marks {check_id} as escalate-only, so this is not auto-patched "
            "regardless of the triage call"
        )

    if classification == FALSE_POSITIVE and len(justification) < 20:
        classification = NEEDS_HUMAN_DESIGN
        should_act = False
        notes.append("a false positive without a defensible written reason is not dismissable")

    if notes:
        justification = (justification + " [" + "; ".join(notes) + "]").strip()

    return TriageResult(
        classification=classification,
        should_act=should_act,
        justification=justification,
        confidence=float(data.get("confidence") or 0.0),
        blast_radius=data.get("blast_radius") or "unknown",
        decided_by="model",
        model=MODEL,
        tokens_in=usage.get("tokens_in", 0),
        tokens_out=usage.get("tokens_out", 0),
    )


# --------------------------------------------------------------------------
# the deterministic guards -- facts, decided before any API call
# --------------------------------------------------------------------------
def _outage_guard(finding: dict, page_source: str) -> TriageResult | None:
    """Mode 4. The app is not there.

    This is the single most important reliability property in the project: a
    naive factory opens seventeen fix requests when the target goes down. The
    signal is a fact we already hold -- no real content came back -- so it is
    decided here rather than inferred from prose by a model that might waver.
    """
    if finding.get("reachable") is False or not (page_source or "").strip():
        route = finding.get("route") or "the audit target"
        observed = (finding.get("evidence") or "no response body").strip()
        return TriageResult(
            classification=UPSTREAM_OUTAGE,
            should_act=False,
            justification=(
                f"No page content came back from {route}, so every check on it failed for one "
                f"reason: there was nothing there to check. Observed: {observed}. This is an "
                "outage, not a defect list -- patching an app that is simply down would be "
                "acting on evidence that does not exist. Backing off and telling a human."
            ),
            confidence=1.0,
            blast_radius="unknown",
            decided_by="guard",
        )
    return None


def _duplicate_guard(finding: dict, history: list | None) -> TriageResult | None:
    """Whether a fix is already in flight is a lookup, not an opinion."""
    for prior in history or []:
        same = prior.get("check_id") == finding.get("check_id") and prior.get("route") == finding.get("route")
        if same and prior.get("status") in ("in_flight", "open_pr"):
            return TriageResult(
                classification=DUPLICATE,
                should_act=False,
                justification=(
                    f"{finding.get('check_id')} on {finding.get('route')} has the same root cause as "
                    f"run {prior.get('run_id')}, which is already in flight with status "
                    f"{prior.get('status')}. Attaching to that run instead of opening a second one."
                ),
                confidence=1.0,
                blast_radius="contained",
                decided_by="guard",
            )
    return None


def _heuristic(finding: dict, check_id: str | None) -> TriageResult:
    """Used only when no credentials are configured.

    Declared honestly on the span as decided_by=heuristic so nobody -- us
    included -- mistakes a rule for a judgement. It keeps the loop runnable
    before the key lands; it is not the feature.
    """
    if check_id == "BRIEF":
        return TriageResult(NEW_FEATURE, True, "Brief accepted without model triage (no credentials configured).", 0.3, "contained", decided_by="heuristic")
    if _policy_action(check_id) == "escalate":
        return TriageResult(
            NEEDS_HUMAN_DESIGN,
            False,
            f"The audit policy marks {check_id} as escalate-only and no triage credentials are "
            "configured, so this is not auto-patched.",
            0.3,
            "clients",
            decided_by="heuristic",
        )
    return TriageResult(
        AUTOFIX_SAFE,
        True,
        f"{check_id} is marked autofix in the audit policy. Accepted without model triage "
        "(no credentials configured).",
        0.3,
        "contained",
        decided_by="heuristic",
    )


def classify(finding, page_source, file_context, history, *, client=None) -> TriageResult:
    """Decide what to do with one finding, or one brief.

    Guards first (facts), then one model call (judgement), then the rails.
    Never raises: a triage that cannot reach the model declines rather than
    guessing, because the failure mode of guessing is a wrong patch.
    """
    finding = finding or {}
    check_id = finding.get("check_id")
    is_brief = check_id == "BRIEF"

    if not is_brief:
        for guard in (_outage_guard(finding, page_source), _duplicate_guard(finding, history)):
            if guard is not None:
                log.info("triage guard: %s for %s on %s", guard.classification, check_id, finding.get("route"))
                return guard

    if client is None and not _credentials_available():
        log.warning("no API key for provider %s -- triage is running on policy heuristics", llm.provider())
        return _heuristic(finding, check_id)

    user = _brief_prompt(finding, file_context) if is_brief else _finding_prompt(finding, page_source, file_context, history)

    try:
        data, usage = _call_model(SYSTEM_PROMPT, user, client=client)
        return _reconcile(data, usage, check_id)
    except Exception as exc:
        log.error("triage call failed (%s): declining rather than guessing", exc)
        return TriageResult(
            classification=NEEDS_HUMAN_DESIGN,
            should_act=False,
            justification=(
                f"Triage could not reach a decision: {type(exc).__name__}: {exc}. An automated "
                "system that cannot reason about a change must not write one, so this is being "
                "escalated to a human rather than patched on a guess."
            ),
            confidence=0.0,
            blast_radius="unknown",
            decided_by="fallback",
        )

"""
forge/planner.py -- the code-writing agent. Writes features and patches.

Two entry points, ONE prompt shape, one call path:

    plan_fix(finding, triage, file_contents, policy)      -> ChangeSet
    plan_feature(brief_text, existing_routes, templates, policy) -> ChangeSet

A ChangeSet is a list of {path, content, reason} carrying FULL file contents,
never diffs. A diff has to be correct about line numbers and context it cannot
see; a whole file only has to be correct about the file. Whole files are the
reliable choice for a generated change, and they make the pull request readable.

THE ACCEPTANCE CRITERIA ARE THE POLICY, NOT A VIBE
--------------------------------------------------------------------------
Both prompts carry the seventeen checks from policy/audit_policy.yaml as hard
requirements. This is the part that closes the loop: the same policy that finds
the defect is handed to the agent that writes the code, so a feature generated
at 14:00 is written against the standard that will audit it at 14:05. When the
audit later fails a generated page, that is a real failure to meet a stated
requirement -- not a goalpost we moved afterwards.

BLAST RADIUS
--------------------------------------------------------------------------
The agent may only write to pulse/ and tests/. A proposed path outside those is
dropped and logged loudly rather than written -- the factory can change the app
it built, but it cannot change itself. vcs.write_files enforces the same rule at
the filesystem; this is the earlier of the two gates, and it means an attempt
shows up in the trace as a decision rather than as a crash.

KNOWING WHERE THINGS LIVE (the REPO MAP)
--------------------------------------------------------------------------
Being allowed to write a path is not the same as that path being able to do the
job. run_f1e86721 opened on "X-Frame-Options missing on /", justified itself
with "add X-Frame-Options in the security-headers middleware", and then wrote
pulse/routes/security.py -- a route module, which cannot set response headers
for OTHER routes. The header was never set, the re-audit found the finding
exactly as before, and the run was rejected three times and escalated. Nothing
was wrong with VERIFY; the patch could not possibly have closed the finding.

So the prompt now carries a REPO MAP as hard constraints, and for the one case
that provably cannot work -- a response-header finding patched anywhere but
pulse/main.py -- there is a rail here as well as a sentence in the prompt.

FAMILIES: SOME FINDINGS CANNOT BE FIXED ONE AT A TIME
--------------------------------------------------------------------------
S1-S6 are six findings with one fix: a single security-headers middleware.
Closing them individually is unwinnable, because after S1 is closed the
re-audit still reports S2-S6 on that route and the run never comes back clean.
A finding with a `family` in policy/audit_policy.yaml arrives here with every
open sibling on that route attached, and the change has to close all of them at
once. VERIFY checks the whole family, not just the finding that opened the run.

OWNER: ROHIT.
"""
from __future__ import annotations

import json
import logging
import os
import time

from app import llm
from app.models import ChangeSet

log = logging.getLogger("forge.planner")

#: Code generation is the quality-sensitive step, so it does not share triage's
#: model. Override with FORGE_PLANNER_MODEL if the day calls for it.
MODEL = llm.model_for("planner", "FORGE_PLANNER_MODEL")
MAX_TOKENS = int(os.getenv("FORGE_PLANNER_MAX_TOKENS", "16000"))
MAX_EXCERPT = int(os.getenv("FORGE_PLANNER_MAX_EXCERPT", "6000"))

#: The only two places the factory is allowed to write.
WRITABLE_PREFIXES = ("pulse/", "tests/")

#: The family whose fix is a single app-wide middleware, and the one file that
#: can hold it. A route module only runs for its own paths, so a header set
#: there is absent everywhere else -- which is why this is a rail and not advice.
HEADER_FAMILY = "security_headers"
MIDDLEWARE_FILE = "pulse/main.py"
ROUTE_MODULE_PREFIX = "pulse/routes/"

#: Handed to the model as hard constraints, so "where does this go" is not
#: something it has to infer from a file listing it was never shown.
REPO_MAP = """THE REPO MAP -- WHERE THINGS LIVE. THESE ARE HARD CONSTRAINTS, NOT SUGGESTIONS.

  Response headers, middleware, CORS, app-wide configuration, docs_url guards,
  exception handlers, guards for sensitive paths
      -> pulse/main.py, and NOTHING under pulse/routes/.
         A route module cannot set response headers for OTHER routes. Its
         handlers only run for their own paths, so a header set there is absent
         from every other page and the re-audit reports the finding unchanged.
         A patch under pulse/routes/ CANNOT close a header finding. This is not
         a style preference -- it is the exact mistake that got the last three
         attempts at this rejected.

  Page content, alt text, meta description, title, rel="noopener"
      -> pulse/templates/<page>.html

  Route behaviour, and the data a page is given
      -> pulse/routes/<name>.py

  Tests
      -> tests/test_<thing>.py

Plainly: IF THE FINDING IS ABOUT A RESPONSE HEADER, THE ONLY CORRECT FILE IS
pulse/main.py. If you are about to write a route module to fix a header, stop:
that patch cannot work and will be rejected."""

#: What one security-headers middleware has to do, spelled out so it is written
#: once and completely rather than one header per rejected attempt.
MIDDLEWARE_SPEC = """The middleware must be ONE middleware, registered on the app in pulse/main.py,
applied app-wide, and it must set on EVERY response:

  Content-Security-Policy      a real policy, e.g. default-src 'self'
  X-Frame-Options              DENY
  Strict-Transport-Security    with a max-age, e.g. max-age=31536000; includeSubDomains
  X-Content-Type-Options       nosniff
  Referrer-Policy              no-referrer

and it must STRIP the Server header (uvicorn sends its own version, which is
what S6 fires on) along with X-Powered-By if present.

Write it once. Do not add one header per attempt, and do not add a second
middleware next to an existing one -- if pulse/main.py already has a
security-headers middleware, extend that one."""


class PlannerUnavailable(RuntimeError):
    """Raised when no patch can be generated at all.

    The engine turns this into an escalation rather than an error: a factory
    that cannot write a fix should tell a human, not fail silently or invent
    something. See the cut ladder in section 16 -- the documented fallback is a
    lookup table of pre-written fixes keyed by check_id, which is deliberately
    NOT implemented here while pulse/ is still empty: a templated whole-file
    patch written against an app nobody has seen yet would overwrite it.
    """


RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "rationale": {"type": "string", "minLength": 10},
        "files": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "minLength": 1},
                    "content": {"type": "string"},
                    "reason": {"type": "string", "minLength": 5},
                },
                "required": ["path", "content", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["rationale", "files"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You are the code-writing stage of an automated software factory. \
You produce complete, working files for a small FastAPI + Jinja2 web app called Pulse, \
which displays scraped competitor product data.

Return JSON only. No preamble, no explanation outside the JSON, no markdown fences.

RULES THAT DO NOT BEND

1. Return FULL file contents in `content`, never a diff, never a fragment, never an \
elision like "# ... rest of file unchanged". The content you return replaces the file \
on disk in its entirety. If you were given the current content of a file and you are \
changing one line of it, return the whole file with that one line changed.

2. You may only write to paths beginning with `pulse/` or `tests/`. Never propose a \
path outside those two. You cannot modify the factory that runs you.

3. ALWAYS include a test file under `tests/`. A change with no test is not a change we \
can ship, because a fresh audit and a passing test suite are the two independent things \
that let a human approve it. The test must actually exercise what you changed -- assert \
on the specific behaviour, not on `True`.

4. Every file you touch must satisfy the audit policy given below. The policy is the \
acceptance criteria for this change and the same checks will be run against your output \
within five minutes of it shipping. A page that passes its tests but fails the audit \
does not ship.

5. Make the minimal change that achieves the goal. Do not reformat, do not rename, do \
not "improve" code you were not asked to touch, do not add dependencies. Unrelated \
behaviour must survive your change unchanged.

`rationale` is one or two sentences on what you changed and why, written for the human \
who will read the pull request. `reason` on each file is a short phrase saying what that \
particular file does in this change."""


# --------------------------------------------------------------------------
# the shared machinery -- both entry points go through exactly this
# --------------------------------------------------------------------------
def _normalise_path(path) -> str:
    """One spelling for a repo path: forward slashes, no leading ./.

    Every rail in this module keys off this. A path that reaches one rail as
    "pulse/main.py" and another as a Windows-style path is a rail that does not
    fire, and the rails here are the difference between a minimal patch and a
    generated file that deletes the app.
    """
    return (path or "").replace(chr(92), "/").lstrip("./")


def _excerpt(text: str | None, limit: int = MAX_EXCERPT) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text or "(empty)"
    return text[:limit] + f"\n... [truncated, {len(text)} chars total]"


def _render_files(files, limit: int = 3, chars: int = MAX_EXCERPT) -> str:
    files = files if isinstance(files, dict) else {}
    if not files:
        return "(none available)"
    return "\n\n".join(
        f"--- {path} ---\n{_excerpt(content, chars)}" for path, content in list(files.items())[:limit]
    )


def _policy_requirements(policy) -> str:
    """The seventeen checks, rendered as requirements the output must satisfy.

    Passed as the acceptance criteria rather than as advice. If this ends up
    empty the generated code is being written against nothing, so say so loudly
    instead of quietly producing a page that will fail its own audit.
    """
    checks = None
    if isinstance(policy, dict):
        checks = policy.get("checks")
    if checks is None:
        try:
            from app import audit

            checks = audit.load_policy().get("checks")
        except Exception as exc:
            log.warning("could not load the audit policy for the planner prompt: %s", exc)
            checks = None
    if not checks:
        return "(POLICY UNAVAILABLE -- generated code cannot be checked against it)"

    lines = []
    for check in checks:
        family = check.get("family")
        tail = f" [family {family}: these are fixed together, in one change]" if family else ""
        lines.append(
            f"  [{check['id']} {check['severity']}] {check['title']} -- {check['fix_hint']}{tail}"
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------
# families -- findings that share one fix and must be closed together
# --------------------------------------------------------------------------
def _family_name(finding, family) -> str | None:
    """The family this run is closing, from the caller or from the finding."""
    if isinstance(family, dict) and family.get("name"):
        return family["name"]
    return (finding or {}).get("family")


def _family_block(finding, family) -> str:
    """Every open finding in the family, and the one fix that closes them.

    Without this the run is unwinnable: the planner closes S1, the re-audit
    still reports S2-S6 on the same route, VERIFY rejects, and attempt two
    closes S2 and meets S1, S3, S4, S5 and S6 again. Six findings to lose in
    three attempts.
    """
    name = _family_name(finding, family)
    if not name:
        return ""

    findings = list((family or {}).get("findings") or []) if isinstance(family, dict) else []
    if not findings and finding:
        findings = [finding]

    listed = "\n".join(
        f"  {f.get('check_id')} {f.get('severity')} on {f.get('route')} -- {f.get('title')}\n"
        f"      evidence: {f.get('evidence')}"
        for f in findings
    ) or "  (none listed)"

    route = (finding or {}).get("route") or (findings[0].get("route") if findings else "/")
    extra = MIDDLEWARE_SPEC if name == HEADER_FAMILY else ""
    return f"""

THIS FINDING IS PART OF THE `{name}` FAMILY -- CLOSE ALL OF IT IN ONE CHANGE

These {len(findings)} finding(s) are open on {route} and share ONE fix:

{listed}

Closing them one at a time cannot succeed. After you close one the re-audit
still reports the others on this route, the run is rejected, and the next
attempt starts from here again. VERIFY checks that EVERY finding listed above
is gone -- not just the one that opened this run.

{extra}"""


# --------------------------------------------------------------------------
# the placement rail -- the one mistake that provably cannot work
# --------------------------------------------------------------------------
def _placement_for(finding, family) -> dict | None:
    """Where a header fix is allowed to live, or None when it does not apply."""
    if _family_name(finding, family) != HEADER_FAMILY:
        return None
    return {
        "required": MIDDLEWARE_FILE,
        "forbidden_prefix": ROUTE_MODULE_PREFIX,
        "check_id": (finding or {}).get("check_id"),
        "route": (finding or {}).get("route"),
    }


def _placement_problem(changeset, placement) -> str | None:
    """The rejection sentence for a misplaced header patch, or None if it is fine.

    This is a rail rather than only a sentence in the prompt because the failure
    is not a matter of degree: a header written in a route module is absent from
    every other route, so the patch cannot close the finding no matter how well
    it is written. Catching it here costs one re-ask; letting it through costs a
    branch, a test run, two audits and a rejected attempt.
    """
    if not placement:
        return None
    paths = [c.get("path", "") for c in changeset]
    offenders = [p for p in paths if p.startswith(placement["forbidden_prefix"])]
    if not offenders and placement["required"] in paths:
        return None

    lines = [
        f"YOUR PATCH CANNOT CLOSE {placement['check_id']} ON {placement['route']}, "
        "AND WAS NOT WRITTEN TO DISK."
    ]
    if offenders:
        lines.append(
            "You wrote " + ", ".join(offenders) + ". A route module cannot set response headers "
            "for other routes -- its handlers only run for their own paths, so the header would "
            "be absent everywhere else and the re-audit would report this finding unchanged."
        )
    if placement["required"] not in paths:
        lines.append(
            f"You did not touch {placement['required']}. An app-wide response header can only "
            f"come from a middleware registered on the app, and the app is defined in "
            f"{placement['required']}."
        )
    lines.append(
        f"Return the change again with the FULL content of {placement['required']} carrying one "
        f"security-headers middleware, and no file under {placement['forbidden_prefix']}."
    )
    return "\n".join(lines)


def _retry_context(previous) -> str:
    """On a retry the second call must have strictly more information than the first.

    Not "try again". Attempt 2 has to be able to see what attempt 1 actually
    did: WHICH FILES it wrote, the exact reason VERIFY rejected it, and whether
    the finding was still there afterwards. Without those three, attempts 2 and
    3 repeat attempt 1 -- which is exactly what run_f1e86721 did three times,
    writing a route module for a header finding on every pass.
    """
    if not previous:
        return ""
    attempt = previous.get("attempt", 1)
    failure = previous.get("verify", {}) or {}
    files = previous.get("changeset") or []

    paths = list(previous.get("paths") or [f.get("path") for f in files if f.get("path")])
    written = "\n".join("  " + str(path) for path in paths) or "  (no files were produced)"

    rendered = "\n\n".join(
        f"--- {f.get('path')} ---\n{_excerpt(f.get('content'), 2500)}" for f in files[:3]
    ) or "(no files were produced)"

    still_open = previous.get("finding_still_open")
    if still_open is None:
        verdict = "  not recorded"
    elif still_open:
        verdict = (
            "  YES. The finding this run exists to close was STILL THERE in the fresh audit of\n"
            "  your patch. Whatever you changed, it did not change the thing being measured."
        )
    else:
        verdict = "  No -- the target finding was closed, but the change was rejected for the reason below."

    family_open = previous.get("family_still_open") or []
    family_line = ""
    if family_open:
        family_line = (
            "\n\nSTILL OPEN IN THIS FAMILY AFTER YOUR LAST ATTEMPT: " + ", ".join(str(c) for c in family_open)
            + "\n  All of them have to be closed by ONE change. Closing a subset is a rejection."
        )

    diagnosis = ""
    misplaced = [p for p in paths if str(p).startswith(ROUTE_MODULE_PREFIX)]
    if misplaced and still_open:
        diagnosis = (
            "\n\nDIAGNOSIS OF YOUR LAST ATTEMPT: you wrote " + ", ".join(misplaced) + ", which is a "
            "route\nmodule. A route module cannot set response headers for other routes, so the "
            "header\nwas never set and the audit found the finding exactly as before. Editing that "
            "file\nagain cannot work. Write the middleware in " + MIDDLEWARE_FILE + "."
        )

    return f"""

THIS IS ATTEMPT {attempt + 1}. YOUR PREVIOUS ATTEMPT WAS REJECTED.

FILES YOUR PREVIOUS ATTEMPT WROTE -- if one of these was the wrong file, that is the bug:
{written}

WAS THE FINDING STILL PRESENT AFTER YOUR PATCH?
{verdict}{family_line}{diagnosis}

WHY IT WAS REJECTED -- this is the actual output of the verification, not a summary:
  tests passed:        {failure.get('tests_passed')}
  findings closed:     {failure.get('findings_closed')}
  findings introduced: {failure.get('findings_introduced')}
  rejected because:    {failure.get('rejected_reason') or '(see the evidence below)'}
  evidence:            {failure.get('evidence')}

What you produced last time:
{rendered}

Read that failure carefully and fix the actual cause. Do not resubmit the same files.
If the failure says a NEW finding was introduced, your previous patch closed one hole
and opened another -- that is a rejection, not a partial success."""


def _credentials_available() -> bool:
    return llm.credentials_available()


def _call_model(user: str, attempt: int, client=None) -> tuple[dict, dict]:
    """One model call, through the provider abstraction.

    The truncation rail lives here and fires identically on both providers: a
    file cut off mid-write would corrupt the file it replaces, so a truncated
    response is refused rather than shipped. On OpenAI that includes the case
    where hidden reasoning consumed the whole budget and the content came back
    empty -- which looks like a successful call and returns nothing.
    """
    from app import telemetry

    if client is None and not _credentials_available():
        raise PlannerUnavailable(
            f"No {llm.ENV_KEY[llm.provider()]} is configured for provider {llm.provider()!r}, so no "
            "patch can be generated. Escalating to a human rather than shipping a placeholder."
        )

    with telemetry.stage_span("forge.plan.model_call", "plan") as span:
        result = llm.generate(
            system=SYSTEM_PROMPT,
            user=user,
            max_tokens=MAX_TOKENS,
            model=MODEL,
            json_schema=RESPONSE_SCHEMA,
            client=client,
        )
        llm.annotate_span(span, result)
        if span is not None:
            span.set_attribute("plan.model", result.model)
            span.set_attribute("plan.tokens_in", result.input_tokens)
            span.set_attribute("plan.tokens_out", result.output_tokens)
            span.set_attribute("plan.latency_ms", result.latency_ms)
            span.set_attribute("plan.stop_reason", str(result.finish_reason))
            span.set_attribute("plan.attempt", attempt)

    if result.truncated:
        raise PlannerUnavailable(
            f"The model hit the token ceiling mid-file (finish_reason={result.finish_reason}, "
            f"{result.reasoning_tokens} of {result.output_tokens} output tokens were reasoning), so "
            "the returned content is truncated and would corrupt the file it replaces. Refusing to "
            "ship a partial file."
        )
    if result.empty:
        raise PlannerUnavailable(
            f"The model returned no content (finish_reason={result.finish_reason}). Refusing rather "
            "than writing an empty file over a working one."
        )

    usage = {
        "tokens_in": result.input_tokens,
        "tokens_out": result.output_tokens,
        "stop_reason": result.finish_reason,
        "latency_ms": result.latency_ms,
    }
    return json.loads(_strip_fences(result.text)), usage


def _strip_fences(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        text = text.rsplit("```", 1)[0]
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if start != -1 and end > start else text


def _accept(data: dict, usage: dict, attempt: int, file_contents=None) -> ChangeSet:
    """Turn the model's answer into a ChangeSet, enforcing what must be true.

    Two rails:
      * a path outside pulse/ or tests/ is dropped and logged, never written
      * a changeset with no test file is recorded as such rather than passed
        off as complete
    """
    from app import telemetry

    accepted, rejected = [], []
    for entry in data.get("files", []):
        path = _normalise_path(entry.get("path"))
        if not path.startswith(WRITABLE_PREFIXES) or ".." in path:
            rejected.append(path)
            continue
        accepted.append({"path": path, "content": entry.get("content", ""), "reason": entry.get("reason", "")})

    if rejected:
        # Worth seeing in the trace. The factory proposing a write to its own
        # source is a blast-radius event, not a typo.
        log.error("planner proposed %s path(s) outside pulse/ and tests/, refusing: %s", len(rejected), rejected)
        try:
            telemetry.counter("forge_plan_paths_refused_total", len(rejected))
        except Exception:
            pass

    if not accepted:
        raise PlannerUnavailable(
            "Every proposed path was outside pulse/ and tests/, so there is nothing this factory "
            f"is permitted to write. Refused: {rejected}"
        )

    # A whole-file rewrite that deletes most of the file is not a minimal
    # change -- it is the model reconstructing a file it only half looked at.
    # This actually happened: a patch for the /docs guard came back as a
    # one-line main.py and wiped every route in the app.
    # Keyed the way `accepted` is keyed, or the lookup below silently misses.
    # A caller handing us a Windows-style path meant `before` was always empty,
    # the guard never fired, and a patch that replaced the whole app with a
    # 23-line stub was accepted as minimal.
    given = file_contents if isinstance(file_contents, dict) else {}
    originals = {_normalise_path(path): body for path, body in given.items()}
    for entry in accepted:
        before = (originals.get(entry["path"]) or "").strip()
        if not before:
            continue
        before_lines = len(before.splitlines())
        after_lines = len((entry["content"] or "").splitlines())
        if before_lines >= 10 and after_lines < before_lines * 0.5:
            raise PlannerUnavailable(
                f"The patch for {entry['path']} shrinks it from {before_lines} lines to "
                f"{after_lines}, which would delete most of the file rather than change one "
                "thing. Refusing: a minimal fix does not remove code it was not asked about."
            )

    test_included = any(f["path"].startswith("tests/") for f in accepted)
    if not test_included:
        log.warning("planner returned no test file -- the change is incomplete and VERIFY will see that")

    return ChangeSet(
        accepted,
        rationale=data.get("rationale", ""),
        model=MODEL,
        tokens_in=usage.get("tokens_in", 0),
        tokens_out=usage.get("tokens_out", 0),
        attempt=attempt,
        test_included=test_included,
        rejected_paths=rejected,
    )


#: Kept as an exact phrase: it is what the re-ask is recognised by, in the
#: prompt and in tests/test_planner.py.
MISSING_TEST_REASK = (
    "Your previous response contained no file under tests/. Rule 3 is not optional: return the "
    "same change again, with a real test file that exercises the behaviour you changed. Include "
    "every file from your previous response as well."
)


def _violations(changeset, placement) -> list[str]:
    """What is wrong with a changeset before it is worth writing to disk."""
    problems = []
    if not changeset.test_included:
        problems.append(MISSING_TEST_REASK)
    misplaced = _placement_problem(changeset, placement)
    if misplaced:
        problems.append(misplaced)
    return problems


def _generate(user: str, attempt: int, client=None, file_contents=None, placement=None) -> ChangeSet:
    """The one call path. plan_fix and plan_feature differ only in `user`.

    A changeset that is missing its test, or that puts a header fix somewhere it
    cannot work, gets exactly ONE re-ask naming every problem at once. One, not
    a loop: the engine already owns retries, and burning the retry budget here
    would hide the failure from VERIFY.

    A placement violation that survives the re-ask is refused outright. Writing
    it anyway would spend a branch, a test run and two audits to be told what we
    already know -- a header set in a route module is not set on any other route.
    """
    data, usage = _call_model(user, attempt, client=client)
    changeset = _accept(data, usage, attempt, file_contents)
    problems = _violations(changeset, placement)
    if not problems:
        return changeset

    reask = user + "\n\n" + "\n\n".join(problems)
    log.info("re-asking the planner: %s problem(s) with the first response", len(problems))
    data, retry_usage = _call_model(reask, attempt, client=client)
    second = _accept(data, retry_usage, attempt, file_contents)
    second.tokens_in += usage["tokens_in"]
    second.tokens_out += usage["tokens_out"]

    still_misplaced = _placement_problem(second, placement)
    if still_misplaced:
        log.error("planner kept the header fix out of %s after a re-ask", MIDDLEWARE_FILE)
        raise PlannerUnavailable(
            "The patch was written outside " + MIDDLEWARE_FILE + " twice, and a response header "
            "set anywhere else is not set on the route this finding is about. Refusing to spend a "
            "verification run proving that again.\n\n" + still_misplaced
        )
    return second


# --------------------------------------------------------------------------
# entry point 1 -- a finding becomes a patch
# --------------------------------------------------------------------------
def plan_fix(
    finding,
    triage,
    file_contents,
    policy,
    *,
    previous=None,
    attempt=1,
    client=None,
    family=None,
) -> ChangeSet:
    """A finding becomes a patch.

    `family` is {"name": str, "findings": [...]} when this finding belongs to a
    set that shares one fix (S1-S6 and the security-headers middleware). The
    whole set is put in front of the model and the whole set has to be closed --
    see the module docstring for why one at a time cannot converge.
    """
    finding = finding or {}
    triage = triage or {}
    placement = _placement_for(finding, family)
    family_block = _family_block(finding, family)

    if family_block:
        scope = (
            "Close EVERY finding in the family listed above, on "
            + str(finding.get("route"))
            + ", with ONE change. Do not fix findings outside that family -- those are triaged "
            "separately and a patch that changes five unrelated things is a patch no human can "
            "review."
        )
    else:
        scope = (
            "Make the MINIMAL change that closes "
            + str(finding.get("check_id"))
            + " on "
            + str(finding.get("route"))
            + " without touching unrelated behaviour. Do not fix other findings you happen to "
            "notice -- each one is triaged separately and a patch that changes five things is a "
            "patch no human can review."
        )

    user = f"""Close this finding from an audit of an app this factory built.

THE FINDING
  check_id:  {finding.get('check_id')}
  severity:  {finding.get('severity')}
  route:     {finding.get('route')}
  title:     {finding.get('title')}
  evidence:  {finding.get('evidence')}
  fix hint from the policy: {finding.get('suggested_fix_hint')}

WHY THIS ONE IS SAFE TO PATCH AUTOMATICALLY -- triage decided this, act within it
  classification: {triage.get('classification')}
  reasoning:      {triage.get('justification')}
{family_block}

{REPO_MAP}

THE CURRENT CONTENT OF THE FILE THAT SERVES THAT ROUTE
{_render_files(file_contents)}

THE AUDIT POLICY -- your output is checked against every one of these
{_policy_requirements(policy)}

{scope} Return the full content of every file you change, plus a test that fails
before your change and passes after it.{_retry_context(previous)}

Return JSON only."""
    return _generate(user, attempt, client=client, file_contents=file_contents, placement=placement)


# --------------------------------------------------------------------------
# entry point 2 -- a brief becomes a feature
# --------------------------------------------------------------------------
def plan_feature(brief_text, existing_routes, templates, policy, *, previous=None, attempt=1, client=None) -> ChangeSet:
    routes = existing_routes if isinstance(existing_routes, dict) else {}
    listing = "\n".join(f"  - {p}" for p in (routes or existing_routes or [])) or "  (no route files yet)"
    user = f"""Build this feature for Pulse.

THE BRIEF
{_excerpt(brief_text, 3000)}

{REPO_MAP}

EXISTING ROUTE FILES -- match this style, these imports, these conventions
{listing}

{_render_files(routes)}

THE JINJA TEMPLATES, INCLUDING THE BASE TEMPLATE TO EXTEND
{_render_files(templates)}

THE AUDIT POLICY -- THIS IS THE ACCEPTANCE CRITERIA FOR YOUR CODE, NOT ADVICE
{_policy_requirements(policy)}

The page you generate is audited against every check above within five minutes of
shipping, by the same factory that is asking you to write it. So the code you return
MUST already satisfy them. Concretely, and at minimum:
  - security headers on the response: Content-Security-Policy, X-Frame-Options,
    Strict-Transport-Security, X-Content-Type-Options nosniff, Referrer-Policy
  - a non-empty alt attribute on every img element
  - rel="noopener noreferrer" on every link pointing at another origin
  - a title element AND a meta name="description" in the head
  - no secret-shaped strings anywhere in the template, including in comments
  - every internal href you write must resolve to a route that exists

Write the route file, the template, and the test.{_retry_context(previous)}

Return JSON only."""
    return _generate(user, attempt, client=client, file_contents=routes)

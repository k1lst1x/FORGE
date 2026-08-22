"""
forge/verify.py -- two independent verifications.

    verify(changeset, cr) -> VerifyResult

This is the answer to "how do you know the fix is real". Tests say the change
works. A fresh audit says it is not vulnerable. They catch different things, and
neither alone is enough:

  * An agent can write a patch that satisfies a test without closing the hole --
    assert the route returns 200, never check the header. Tests pass, the
    vulnerability is untouched. The audit catches that.
  * An agent can close a hole and break the page doing it. The audit is happy,
    the app is broken. The tests catch that.

A patch that closes one finding while opening another is REJECTED, before a
human ever sees it. findings_introduced is a hard blocker, not a warning: a fix
that trades a MED for a HIGH is not a fix.

HOW THE "AFTER" STATE IS MEASURED
--------------------------------------------------------------------------
The candidate is served on a scratch port FROM THE FACTORY WORKTREE -- the tree
vcs.write_files just wrote the patch into -- and audited there. The live app is
never restarted against unverified code. If a patch is bad enough that the app
will not boot, the candidate fails to start and that IS the verification result,
with the traceback as evidence. Breaking the app is caught before the pull
request exists, not after the merge.

Both checks run with the worktree as their working directory, and that is load
bearing rather than tidy: the changeset only exists there. Run pytest from the
main checkout and it is handed a path to a test file that was never written to
that tree, so every attempt fails "file not found" no matter what the patch
says. Serve the app from the main checkout and the audit measures the UNPATCHED
app, so the finding is always still present and every attempt is rejected. Both
look exactly like a bad patch in the evidence.

A FAMILY IS CLOSED TOGETHER OR NOT AT ALL
--------------------------------------------------------------------------
When the finding belongs to a family in policy/audit_policy.yaml -- S1-S6 all
share one security-headers middleware -- every open sibling on that route has to
be gone from the fresh audit, not just the finding that opened the run. A patch
that closes S1 and leaves S2-S6 is not most of a fix; it is a change that leaves
the route exactly as unshippable as it was.

FAILING SAFE WHEN THERE IS NO BASELINE
--------------------------------------------------------------------------
"No NEW findings" needs a before to compare against. When no baseline can be
established -- the live app is down, so every check trivially "failed" -- a
naive diff would report those phantom findings as CLOSED and pass a patch that
fixed nothing. So with no baseline the rule tightens rather than relaxes: the
affected routes must come back with zero HIGH findings at all.

OWNER: ROHIT.
"""
from __future__ import annotations

import logging
import os
import re
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from forge import config
from forge.models import VerifyResult

log = logging.getLogger("forge.verify")

PYTEST_TIMEOUT = int(os.getenv("FORGE_VERIFY_PYTEST_TIMEOUT", "120"))
STARTUP_TIMEOUT = float(os.getenv("FORGE_VERIFY_STARTUP_TIMEOUT", "20"))
PULSE_APP = os.getenv("PULSE_APP", "pulse.main:app")

#: Route decorators in a generated FastAPI file, so a feature run knows which
#: route it just created without being told.
ROUTE_DECORATOR = re.compile(r"@(?:app|router)\.(?:get|post|put|delete)\(\s*[\"']([^\"']+)[\"']")


def candidate_cwd() -> str:
    """Where the patched code actually lives: the factory worktree.

    vcs.write_files writes ONLY into the worktree, so a check that runs anywhere
    else is not checking the patch. Falls back to the process directory when
    there is no worktree -- with a loud warning, because in that state the
    "after" measurement is of the unpatched tree and every verdict it produces
    is meaningless.
    """
    try:
        from forge import vcs

        if vcs.WORKTREE.exists():
            return str(vcs.WORKTREE)
        log.warning(
            "no factory worktree at %s -- verifying the process directory instead, which does "
            "NOT contain the patch. Any verdict from this run is about the unpatched tree.",
            vcs.WORKTREE,
        )
    except Exception as exc:  # vcs is Damir's; a wobble there must not kill VERIFY
        log.warning("could not locate the factory worktree: %s", exc)
    return os.getcwd()


# --------------------------------------------------------------------------
# check one: the tests
# --------------------------------------------------------------------------
def _test_paths(changeset) -> list[str]:
    return [c["path"] for c in changeset if c.get("path", "").startswith("tests/")]


def run_tests(changeset, cwd: str | None = None) -> tuple[bool, str]:
    """Run pytest over the tests that accompany this change.

    Scope is the changeset's own test files, widened by FORGE_VERIFY_TEST_PATHS.
    Deliberately NOT the whole tests/ directory: the factory's own test suite
    lives there too, and a failing factory test must not block an app patch.
    They are different suites that happen to share a folder.
    """
    paths = _test_paths(changeset)
    extra = [p for p in (os.getenv("FORGE_VERIFY_TEST_PATHS") or "").split(",") if p.strip()]
    paths.extend(p.strip() for p in extra)

    if not paths:
        return False, (
            "No test file accompanied this change, so there is nothing to run. A change we cannot "
            "test is a change we cannot verify, and an unverifiable change does not ship."
        )

    command = [sys.executable, "-m", "pytest", *paths, "-q", "--no-header", "-p", "no:cacheprovider"]
    working = cwd or candidate_cwd()
    log.info("verify: running %s (in %s)", " ".join(command), working)
    try:
        completed = subprocess.run(
            command,
            cwd=working,
            capture_output=True,
            text=True,
            timeout=PYTEST_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return False, f"pytest did not finish within {PYTEST_TIMEOUT}s and was killed. Treating as a failure."
    except Exception as exc:
        return False, f"pytest could not be run: {type(exc).__name__}: {exc}"

    output = ((completed.stdout or "") + (completed.stderr or "")).strip()
    passed = completed.returncode == 0
    # The tail is where pytest puts the failure summary a human needs.
    tail = "\n".join(output.splitlines()[-40:])
    return passed, tail or f"pytest exited {completed.returncode} with no output"


# --------------------------------------------------------------------------
# check two, part one: stand the branch up somewhere harmless
# --------------------------------------------------------------------------
def _free_port() -> int:
    fixed = os.getenv("FORGE_VERIFY_PORT")
    if fixed:
        return int(fixed)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _wait_until_serving(port: int, process, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            return False  # it died on the way up
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.15)
    return False


@contextmanager
def serve_candidate(port: int | None = None, cwd: str | None = None):
    """Serve the branch working tree on a scratch port. Yields (base_url, error).

    The live app is never restarted against unverified code. If the candidate
    will not boot -- a patch with a syntax error, a bad import, a route that
    raises at import time -- error carries the startup output and base_url is
    None, which is itself the verification result.
    """
    port = port or _free_port()
    working = cwd or candidate_cwd()
    command = [sys.executable, "-m", "uvicorn", PULSE_APP, "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"]
    log.info("verify: serving the candidate from %s on port %s", working, port)
    process = None
    try:
        try:
            process = subprocess.Popen(
                command,
                cwd=working,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
        except Exception as exc:
            yield None, f"could not launch the candidate app: {type(exc).__name__}: {exc}"
            return

        if not _wait_until_serving(port, process, STARTUP_TIMEOUT):
            output = ""
            try:
                process.kill()
                output = (process.communicate(timeout=5)[0] or "").strip()
            except Exception:
                pass
            tail = "\n".join(output.splitlines()[-25:])
            yield None, (
                f"the patched app did not start within {STARTUP_TIMEOUT}s on port {port}. "
                f"A change that stops the app booting is not a change that ships.\n{tail}"
            )
            return

        yield f"http://127.0.0.1:{port}", None
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except Exception:
                process.kill()


def routes_under_test(changeset, cr) -> list[str]:
    """Which routes this change actually affects.

    For a fix, the finding's route. For a feature, the routes are read out of
    the generated file's own decorators -- the factory does not have to be told
    which route it just wrote.
    """
    routes: list[str] = []
    if getattr(cr, "route", None):
        routes.append(cr.route)
    for change in changeset:
        if change.get("path", "").startswith("pulse/"):
            routes.extend(ROUTE_DECORATOR.findall(change.get("content") or ""))

    # A guard the factory just wrote for /.env or /admin is a route in the
    # source, but it is not a PAGE. Auditing it as one produces six header
    # findings against a path whose whole job is to refuse -- and those persist
    # into the catalog as graded pages. Drop them.
    blocked = set(_exposure_paths())
    routes = [r for r in routes if r not in blocked]
    # "/" carries the app-level checks: exposed paths, stack traces, docs.
    if "/" not in routes:
        routes.append("/")
    seen, ordered = set(), []
    for route in routes:
        if route not in seen:
            seen.add(route)
            ordered.append(route)
    return ordered


def _exposure_paths() -> set:
    """Paths the policy probes for exposure. Never pages in their own right."""
    try:
        from forge import audit

        return set(audit.load_policy().get("exposure_paths") or [])
    except Exception:
        return {"/.env", "/.git/config", "/admin", "/debug", "/docs"}


def new_routes(changeset) -> list[str]:
    """Routes this changeset introduces -- a feature run must land these clean."""
    found: list[str] = []
    for change in changeset:
        if change.get("path", "").startswith("pulse/"):
            found.extend(ROUTE_DECORATOR.findall(change.get("content") or ""))
    blocked = _exposure_paths()
    return [r for r in found if r not in blocked]


# --------------------------------------------------------------------------
# check two, part two: what changed between before and after
# --------------------------------------------------------------------------
def _ids(findings) -> set:
    return {f.get("finding_id") for f in findings or []}


def _high(findings) -> list:
    return [f for f in findings or [] if (f.get("severity") or "").upper() == "HIGH"]


def _describe(finding: dict) -> str:
    return f"{finding.get('check_id')} on {finding.get('route')} ({finding.get('severity')})"


def family_findings(cr) -> list[dict]:
    """The open siblings this run also has to close, put there by CONTEXT.

    Empty for a finding that stands alone, which is most of them.
    """
    if cr is None:
        return []
    context = getattr(cr, "context", None) or {}
    return [f for f in (context.get("family_findings") or []) if isinstance(f, dict)]


def compare(cr, before, after, changeset) -> tuple[list, list, bool, list[str], list[dict], str]:
    """Decide whether the candidate is better than what it replaces.

    Returns (findings_closed, findings_introduced, ok, evidence lines,
    findings_still_open, rejected_reason). findings_introduced holds the HIGH
    findings that were not there before -- the blocking set. Anything new at MED
    or LOW is reported but does not block.
    """
    notes: list[str] = []
    reasons: list[str] = []
    baseline = bool(before is not None and getattr(before, "reachable", False))
    after_findings = list(getattr(after, "findings", []) or [])
    before_findings = list(getattr(before, "findings", []) or []) if baseline else []

    before_ids, after_ids = _ids(before_findings), _ids(after_findings)
    closed = [f for f in before_findings if f.get("finding_id") not in after_ids]
    appeared = [f for f in after_findings if f.get("finding_id") not in before_ids]
    introduced = _high(appeared)
    minor = [f for f in appeared if f not in introduced]

    if not baseline:
        # No before to diff against. Tighten instead of guessing: nothing HIGH
        # may be present at all. A phantom "closed" list from an unreachable
        # baseline would otherwise pass a patch that fixed nothing.
        introduced = _high(after_findings)
        closed = []
        notes.append(
            "No baseline audit was available, so the stricter rule applies: the affected routes "
            "must come back with zero HIGH findings, not merely no NEW ones."
        )

    ok = True
    still_open: list[dict] = []

    if cr is not None and getattr(cr, "finding", None):
        target = cr.finding.get("finding_id")
        if target in after_ids:
            ok = False
            still_open.append(cr.finding)
            notes.append(
                f"The finding this run exists to close, {_describe(cr.finding)}, is STILL PRESENT "
                "after the patch. The change did not do what it was for."
            )
            reasons.append(f"{_describe(cr.finding)} is still present after the patch")
        else:
            if target and target not in _ids(closed):
                closed = closed + [cr.finding]
            notes.append(f"The target finding {_describe(cr.finding)} is gone from the fresh audit.")

        # A family is closed together or not at all. Closing S1 while S2-S6 stay
        # open is not most of a fix -- the route is exactly as unshippable as it
        # was, and the next attempt would be handed the same five findings.
        siblings = [f for f in family_findings(cr) if f.get("finding_id") != target]
        if siblings:
            family = cr.finding.get("family") or "family"
            open_now = [f for f in siblings if f.get("finding_id") in after_ids]
            gone = [f for f in siblings if f.get("finding_id") not in after_ids]
            closed = closed + [f for f in gone if f.get("finding_id") not in _ids(closed)]
            if open_now:
                ok = False
                still_open.extend(open_now)
                notes.append(
                    f"The `{family}` family was not closed in one change. Still open after the "
                    "patch: " + "; ".join(_describe(f) for f in open_now)
                    + ". These share a single fix, so a patch that closes some of them leaves the "
                    "route in the same state and the next audit reports the rest."
                )
                reasons.append(
                    f"{len(open_now)} of {len(siblings) + 1} `{family}` findings on "
                    f"{cr.finding.get('route')} are still open"
                )
            else:
                notes.append(
                    f"All {len(siblings) + 1} open `{family}` findings on "
                    f"{cr.finding.get('route')} are gone from the fresh audit."
                )
    else:
        # A feature run: the routes it created must be clean on their own terms.
        created = new_routes(changeset)
        dirty = [f for f in _high(after_findings) if f.get("route") in created]
        if dirty:
            ok = False
            still_open.extend(dirty)
            notes.append(
                "The generated route(s) "
                + ", ".join(sorted(set(created)))
                + " came back with HIGH findings: "
                + "; ".join(_describe(f) for f in dirty)
                + ". The audit policy was the acceptance criteria for this code."
            )
            reasons.append(
                "the generated route(s) came back with "
                + str(len(dirty))
                + " HIGH finding(s)"
            )
        elif created:
            notes.append(f"Generated route(s) {', '.join(sorted(set(created)))} have no HIGH findings.")

    if introduced:
        ok = False
        if baseline:
            notes.append(
                "REJECTED -- this change introduces "
                + str(len(introduced))
                + " HIGH finding(s) that were not there before: "
                + "; ".join(_describe(f) for f in introduced)
                + ". A patch that closes one hole and opens another is not a fix."
            )
            reasons.append(
                "it introduces " + str(len(introduced)) + " HIGH finding(s) that were not there before"
            )
        else:
            # Say what was actually measured. With no baseline we cannot claim
            # these are new, only that they are present, and the evidence a
            # human reads must not overstate what we know.
            notes.append(
                "REJECTED -- the patched candidate still serves "
                + str(len(introduced))
                + " HIGH finding(s): "
                + "; ".join(_describe(f) for f in introduced)
                + ". With no baseline these cannot be attributed to this change, only observed."
            )
            reasons.append(
                "the candidate still serves " + str(len(introduced)) + " HIGH finding(s)"
            )
    if minor:
        notes.append(
            "Also new, not blocking: " + "; ".join(_describe(f) for f in minor)
        )

    return closed, introduced, ok, notes, still_open, "; ".join(reasons)


# --------------------------------------------------------------------------
# the two checks, in order
# --------------------------------------------------------------------------
def _baseline(routes) -> object | None:
    """Audit what is live now, to diff the candidate against.

    Tolerant on purpose: a missing baseline tightens the rule in compare()
    rather than failing the run outright.
    """
    from forge import audit as audit_mod

    try:
        return audit_mod.run_audit(config.PULSE_BASE_URL, routes)
    except Exception as exc:
        log.warning("verify could not establish a baseline: %s", exc)
        return None


def verify(changeset, cr) -> VerifyResult:
    """Tests, then a fresh audit. Both must pass for a change to reach a human."""
    from forge import audit as audit_mod

    changeset = list(changeset or [])
    routes = routes_under_test(changeset, cr)
    attempt = int(getattr(cr, "attempts", 0) or 0) + 1

    # ---- check one: does it work ----
    tests_passed, test_output = run_tests(changeset)
    if not tests_passed:
        return VerifyResult(
            ok=False,
            tests_passed=False,
            attempt=attempt,
            tests_output=test_output,
            rejected_reason="the tests that accompany this change did not pass",
            evidence="TESTS FAILED -- not auditing a change that does not pass its own tests.\n\n" + test_output,
        )

    # ---- check two: is it still not vulnerable ----
    before = _baseline(routes)
    after = None
    startup_error = None

    with serve_candidate() as (base_url, error):
        if error:
            startup_error = error
        else:
            try:
                after = audit_mod.run_audit(base_url, routes)
            except Exception as exc:
                startup_error = f"the fresh audit of the candidate failed to run: {type(exc).__name__}: {exc}"

    if after is None:
        return VerifyResult(
            ok=False,
            tests_passed=True,
            attempt=attempt,
            tests_output=test_output,
            audit_before=before.as_dict() if before is not None else {},
            rejected_reason="the patched candidate could not be audited: "
            + (startup_error or "unknown startup failure")[:200],
            evidence=(
                "TESTS PASSED, BUT THE CANDIDATE COULD NOT BE AUDITED.\n\n"
                + (startup_error or "unknown startup failure")
                + "\n\nA change whose result cannot be measured does not ship."
            ),
        )

    closed, introduced, ok, notes, still_open, rejected_reason = compare(cr, before, after, changeset)

    evidence = [
        "TESTS PASSED.",
        "",
        f"Audited {', '.join(routes)} on the patched candidate: "
        f"{len(after.findings)} finding(s), {len(after.findings_high)} HIGH, worst grade {after.worst_grade}.",
    ]
    if before is not None and before.reachable:
        evidence.append(
            f"Baseline for comparison: {len(before.findings)} finding(s), {len(before.findings_high)} HIGH."
        )
    evidence.append("")
    evidence.extend(notes)
    evidence.append("")
    evidence.append("VERDICT: " + ("verified -- tests pass and the audit is clean" if ok else "rejected"))

    result = VerifyResult(
        ok=ok,
        tests_passed=True,
        attempt=attempt,
        tests_output=test_output,
        audit_before=before.as_dict() if before is not None else {},
        audit_after=after.as_dict(),
        findings_closed=[f.get("finding_id") for f in closed],
        findings_introduced=[f.get("finding_id") for f in introduced],
        findings_still_open=[
            {"finding_id": f.get("finding_id"), "check_id": f.get("check_id"),
             "route": f.get("route"), "severity": f.get("severity")}
            for f in still_open
        ],
        rejected_reason="" if ok else (rejected_reason or "the fresh audit did not come back clean"),
        evidence="\n".join(evidence).strip(),
    )
    log.info(
        "verify %s attempt %s: ok=%s closed=%s introduced=%s still_open=%s",
        getattr(cr, "run_id", "?"),
        attempt,
        result.ok,
        len(result.findings_closed),
        len(result.findings_introduced),
        len(result.findings_still_open),
    )
    return result

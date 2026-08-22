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
The candidate is served on a scratch port from the branch working tree and
audited there -- the live app is never restarted against unverified code. If a
patch is bad enough that the app will not boot, the candidate fails to start and
that IS the verification result, with the traceback as evidence. Breaking the
app is caught before the pull request exists, not after the merge.

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
    log.info("verify: running %s", " ".join(command))
    try:
        completed = subprocess.run(
            command,
            cwd=cwd or os.getcwd(),
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
def serve_candidate(port: int | None = None):
    """Serve the branch working tree on a scratch port. Yields (base_url, error).

    The live app is never restarted against unverified code. If the candidate
    will not boot -- a patch with a syntax error, a bad import, a route that
    raises at import time -- error carries the startup output and base_url is
    None, which is itself the verification result.
    """
    port = port or _free_port()
    command = [sys.executable, "-m", "uvicorn", PULSE_APP, "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"]
    process = None
    try:
        try:
            process = subprocess.Popen(
                command,
                cwd=os.getcwd(),
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


def compare(cr, before, after, changeset) -> tuple[list, list, bool, list[str]]:
    """Decide whether the candidate is better than what it replaces.

    Returns (findings_closed, findings_introduced, ok, evidence lines).
    findings_introduced holds the HIGH findings that were not there before --
    the blocking set. Anything new at MED or LOW is reported but does not block.
    """
    notes: list[str] = []
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

    if cr is not None and getattr(cr, "finding", None):
        target = cr.finding.get("finding_id")
        still_open = target in after_ids
        if still_open:
            ok = False
            notes.append(
                f"The finding this run exists to close, {_describe(cr.finding)}, is STILL PRESENT "
                "after the patch. The change did not do what it was for."
            )
        else:
            if target and target not in _ids(closed):
                closed = closed + [cr.finding]
            notes.append(f"The target finding {_describe(cr.finding)} is gone from the fresh audit.")
    else:
        # A feature run: the routes it created must be clean on their own terms.
        created = new_routes(changeset)
        dirty = [f for f in _high(after_findings) if f.get("route") in created]
        if dirty:
            ok = False
            notes.append(
                "The generated route(s) "
                + ", ".join(sorted(set(created)))
                + " came back with HIGH findings: "
                + "; ".join(_describe(f) for f in dirty)
                + ". The audit policy was the acceptance criteria for this code."
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
    if minor:
        notes.append(
            "Also new, not blocking: " + "; ".join(_describe(f) for f in minor)
        )

    return closed, introduced, ok, notes


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

    # ---- check one: does it work ----
    tests_passed, test_output = run_tests(changeset)
    if not tests_passed:
        return VerifyResult(
            ok=False,
            tests_passed=False,
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
            audit_before=before.as_dict() if before is not None else {},
            evidence=(
                "TESTS PASSED, BUT THE CANDIDATE COULD NOT BE AUDITED.\n\n"
                + (startup_error or "unknown startup failure")
                + "\n\nA change whose result cannot be measured does not ship."
            ),
        )

    closed, introduced, ok, notes = compare(cr, before, after, changeset)

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
        audit_before=before.as_dict() if before is not None else {},
        audit_after=after.as_dict(),
        findings_closed=[f.get("finding_id") for f in closed],
        findings_introduced=[f.get("finding_id") for f in introduced],
        evidence="\n".join(evidence).strip(),
    )
    log.info(
        "verify %s: ok=%s closed=%s introduced=%s",
        getattr(cr, "run_id", "?"),
        result.ok,
        len(result.findings_closed),
        len(result.findings_introduced),
    )
    return result

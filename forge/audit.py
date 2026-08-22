"""
forge/audit.py -- the check suite. The heart of the project.

Every five minutes this fetches every page the app serves and runs seventeen
checks against it. All seventeen are real classes of production defect, all are
detectable from outside the app, and most are fixable by editing one file.

Nothing here is planted. Ask any model to write you a quick FastAPI page and it
will not add security headers, it will leave /docs open, and half the time it
will put an example key in a comment. The factory generates code with exactly
those flaws and then catches itself.

Evidence discipline: every finding carries a short factual string describing
what was OBSERVED, not an opinion about it. "GET /docs returned 200 with an
OpenAPI schema listing 11 endpoints" -- not "the docs endpoint is insecure".
A human reads these, and so does the agent that writes the patch.

Secret discipline: S10 never puts a matched secret in the evidence. It reports
the shape, the length and the surrounding context with the match redacted. The
findings get written to Port and to logs; a scanner that leaks the thing it
found is worse than no scanner.

OWNER: ROHIT.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
import yaml
from bs4 import BeautifulSoup

from forge import config, telemetry
from forge.models import GRADE_VALUE, AuditResult, grade_for

log = logging.getLogger("forge.audit")

FETCH_TIMEOUT = 10.0
PROBE_ORIGIN = "https://forge-audit.invalid"  # elicits a CORS response for S8
BAD_ROUTE = "/__forge_audit_probe__"  # deliberately bad route, for S11

_POLICY_CACHE: dict[str, dict] = {}


def load_policy(path: str | None = None) -> dict:
    """Load and index the policy. The 17 checks are data, not code."""
    path = path or config.POLICY_PATH
    cached = _POLICY_CACHE.get(path)
    if cached is not None:
        return cached
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    raw["by_id"] = {c["id"]: c for c in raw.get("checks", [])}
    _POLICY_CACHE[path] = raw
    return raw


def _threshold(policy: dict, name: str, default):
    return (policy.get("thresholds") or {}).get(name, default)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _finding_id(check_id: str, route: str) -> str:
    """Deterministic, so the same defect on the same route is the same finding
    across runs. Dedupe and the occurrence count depend on this."""
    digest = hashlib.sha1(f"{check_id}|{route}".encode("utf-8")).hexdigest()
    return "f_" + digest[:4]


def _finding(policy: dict, check_id: str, route: str, evidence: str, reachable: bool = True) -> dict:
    spec = policy["by_id"][check_id]
    return {
        "finding_id": _finding_id(check_id, route),
        "check_id": check_id,
        "severity": spec["severity"],
        "route": route,
        "title": spec["title"],
        "category": spec["category"],
        "scope": spec.get("scope", "page"),
        "action": spec.get("action", "autofix"),
        "evidence": evidence,
        "first_seen": _now_iso(),
        "occurrences": 1,
        "suggested_fix_hint": spec["fix_hint"],
        "reachable": reachable,
    }


def _redact(match: str) -> str:
    """Report the shape of a secret, never the secret."""
    head = match[:4]
    return f"{head}...[redacted, {len(match)} chars]"


def _context_around(body: str, start: int, end: int, window: int = 36) -> str:
    """The text around a match, with the match itself redacted, on one line."""
    before = body[max(0, start - window) : start]
    after = body[end : end + window]
    snippet = f"{before}{_redact(body[start:end])}{after}"
    return re.sub(r"\s+", " ", snippet).strip()


# --------------------------------------------------------------------------
# the fetch layer
# --------------------------------------------------------------------------
@dataclass
class Fetched:
    """One HTTP response, or the fact that there was not one."""

    route: str
    url: str
    status: int | None = None
    headers: dict = field(default_factory=dict)
    cookies: list = field(default_factory=list)
    body: str = ""
    elapsed_ms: float = 0.0
    ok: bool = False  # did we get an HTTP response at all
    error: str | None = None

    @property
    def has_content(self) -> bool:
        return self.ok and bool(self.body.strip())


def _fetch(client: httpx.Client, base_url: str, route: str, origin: bool = False) -> Fetched:
    url = urljoin(base_url.rstrip("/") + "/", route.lstrip("/"))
    headers = {"User-Agent": "forge-audit/3.0"}
    if origin:
        headers["Origin"] = PROBE_ORIGIN
    started = time.perf_counter()
    try:
        response = client.get(url, headers=headers)
    except Exception as exc:
        return Fetched(
            route=route,
            url=url,
            elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
        )
    return Fetched(
        route=route,
        url=url,
        status=response.status_code,
        headers={k.lower(): v for k, v in response.headers.items()},
        cookies=list(response.headers.get_list("set-cookie")),
        body=response.text or "",
        elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
        ok=True,
    )


def _describe(fetched: Fetched) -> str:
    """A factual tail for evidence strings: what we were looking at."""
    if not fetched.ok:
        return f"route fetch failed -- {fetched.error}"
    return f"observed on {fetched.status} response for {fetched.route}"


# --------------------------------------------------------------------------
# security: headers  (S1-S8)
# --------------------------------------------------------------------------
def check_headers(fetched: Fetched, policy: dict) -> list[dict]:
    route, headers = fetched.route, fetched.headers
    reachable = fetched.ok
    where = _describe(fetched)
    out: list[dict] = []

    def add(check_id: str, evidence: str) -> None:
        out.append(_finding(policy, check_id, route, evidence, reachable=reachable))

    csp = headers.get("content-security-policy", "").strip()
    if not csp:
        add("S1", f"No Content-Security-Policy header, {where}")

    xfo = headers.get("x-frame-options", "").strip()
    if not xfo and "frame-ancestors" not in csp.lower():
        add("S2", f"Neither X-Frame-Options nor a CSP frame-ancestors directive, {where}")

    if not headers.get("strict-transport-security", "").strip():
        add("S3", f"No Strict-Transport-Security header, {where}")

    nosniff = headers.get("x-content-type-options", "").strip().lower()
    if nosniff != "nosniff":
        seen = f'"{nosniff}"' if nosniff else "absent"
        add("S4", f"X-Content-Type-Options is {seen}, expected nosniff, {where}")

    if not headers.get("referrer-policy", "").strip():
        add("S5", f"No Referrer-Policy header, {where}")

    leaks = []
    for name in ("server", "x-powered-by"):
        value = headers.get(name, "").strip()
        if value and re.search(r"\d+\.\d+|/\s*\d", value):
            leaks.append(f'{name}: "{value}"')
    if leaks:
        add("S6", "Version-bearing header(s) " + "; ".join(leaks) + f", {where}")

    cookie_problems = []
    for raw in fetched.cookies:
        name = raw.split("=", 1)[0].strip()
        low = raw.lower()
        missing = [f for f in ("secure", "httponly", "samesite") if f not in low]
        if missing:
            cookie_problems.append(f'"{name}" missing {", ".join(m.title() for m in missing)}')
    if cookie_problems:
        add("S7", "Set-Cookie " + "; ".join(cookie_problems) + f", {where}")

    acao = headers.get("access-control-allow-origin", "").strip()
    acac = headers.get("access-control-allow-credentials", "").strip().lower()
    if acac == "true" and acao in ("*", PROBE_ORIGIN):
        shown = "*" if acao == "*" else f"reflected back as {acao}"
        add("S8", f"Access-Control-Allow-Origin is {shown} while Allow-Credentials is true, {where}")

    return out


# --------------------------------------------------------------------------
# security: secret-shaped strings  (S10)
# --------------------------------------------------------------------------
def _secret_patterns(policy: dict) -> list[tuple[str, re.Pattern]]:
    hex_len = _threshold(policy, "secret_min_hex", 32)
    b64_len = _threshold(policy, "secret_min_b64", 40)
    return [
        ("an OpenAI-style key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
        ("an AWS access key id", re.compile(r"AKIA[0-9A-Z]{16}")),
        ("a PEM private key header", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
        ("a PEM block header", re.compile(r"-----BEGIN [A-Z ]+-----")),
        (f"a bare hex string of {hex_len}+ chars", re.compile(r"\b[0-9a-fA-F]{%d,}\b" % hex_len)),
        (f"a base64 blob of {b64_len}+ chars", re.compile(r"\b[A-Za-z0-9+/]{%d,}={0,2}\b" % b64_len)),
    ]


def check_secrets(fetched: Fetched, policy: dict) -> list[dict]:
    """Regex the HTML and any inline JS. Evidence carries the context, never the match.

    This check earns its false positives. A Subresource Integrity hash is a
    long base64 blob and will fire here -- which is correct behaviour for a
    scanner and exactly why triage has a FALSE_POSITIVE classification.
    """
    if not fetched.has_content:
        return []
    body = fetched.body
    hits: list[str] = []
    for description, pattern in _secret_patterns(policy):
        match = pattern.search(body)
        if not match:
            continue
        context = _context_around(body, match.start(), match.end())
        hits.append(f"{description} at offset {match.start()} in ...{context}...")
    if not hits:
        return []
    return [
        _finding(
            policy,
            "S10",
            fetched.route,
            f"{len(hits)} secret-shaped string(s) in the served HTML: " + " | ".join(hits[:3]),
        )
    ]


# --------------------------------------------------------------------------
# security: exposure and error leakage  (S9, S11, S12) -- probed once per run
# --------------------------------------------------------------------------
def probe_exposure(client: httpx.Client, base_url: str, policy: dict, app_route: str) -> list[dict]:
    """S9 and S12. Anything that is not a 404 is reachable, and that is the fact."""
    paths = policy.get("exposure_paths") or []
    sensitive, docs = [], []
    for path in paths:
        got = _fetch(client, base_url, path)
        if not got.ok or got.status == 404:
            continue
        size = len(got.body.encode("utf-8", "ignore"))
        ctype = got.headers.get("content-type", "unknown").split(";")[0]
        detail = f"GET {path} returned {got.status} ({ctype}, {size} bytes)"
        if path == "/docs":
            schema = _fetch(client, base_url, "/openapi.json")
            if schema.ok and schema.status == 200:
                try:
                    import json

                    count = len(json.loads(schema.body).get("paths", {}))
                    detail = f"GET {path} returned {got.status} with an OpenAPI schema listing {count} endpoints"
                except Exception:
                    pass
            docs.append(detail)
        else:
            sensitive.append(detail)

    out = []
    if sensitive:
        out.append(_finding(policy, "S9", app_route, "; ".join(sensitive)))
    if docs:
        out.append(_finding(policy, "S12", app_route, "; ".join(docs)))
    return out


TRACEBACK_MARKERS = (
    "Traceback (most recent call last)",
    'File "',
    "werkzeug.debug",
    "django.views.debug",
    "DEBUG = True",
)


def probe_stack_trace(client: httpx.Client, base_url: str, policy: dict, app_route: str) -> list[dict]:
    """S11. Request a deliberately bad route and read what comes back."""
    got = _fetch(client, base_url, BAD_ROUTE)
    if not got.ok or not got.body:
        return []
    found = [m for m in TRACEBACK_MARKERS if m in got.body]
    if not found:
        return []
    evidence = (
        f"GET {BAD_ROUTE} returned {got.status} with debug markers in the body: "
        + ", ".join(f'"{m}"' for m in found[:3])
    )
    return [_finding(policy, "S11", app_route, evidence)]


# --------------------------------------------------------------------------
# quality and performance  (Q1-Q4, P1)
# --------------------------------------------------------------------------
def check_dom(fetched: Fetched, policy: dict, client: httpx.Client, base_url: str, link_cache: dict) -> list[dict]:
    if not fetched.has_content:
        # No body means no DOM to judge. Q3 still fires: there is no title.
        return [_finding(policy, "Q3", fetched.route, f"No document returned, {_describe(fetched)}", reachable=fetched.ok)]

    soup = BeautifulSoup(fetched.body, "html.parser")
    route = fetched.route
    out: list[dict] = []

    images = soup.find_all("img")
    missing_alt = [i for i in images if not (i.get("alt") or "").strip()]
    if missing_alt:
        shown = ", ".join(f'src="{i.get("src", "?")}"' for i in missing_alt[:3])
        out.append(
            _finding(policy, "Q1", route, f"{len(missing_alt)} of {len(images)} img elements have no alt text: {shown}")
        )

    host = urlparse(base_url).netloc
    external_bad = []
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        parsed = urlparse(href)
        if parsed.scheme in ("http", "https") and parsed.netloc and parsed.netloc != host:
            rel = " ".join(anchor.get("rel") or []).lower()
            if "noopener" not in rel:
                external_bad.append(href)
    if external_bad:
        shown = ", ".join(external_bad[:3])
        out.append(
            _finding(policy, "Q2", route, f"{len(external_bad)} external link(s) without rel=noopener: {shown}")
        )

    title = (soup.title.string or "").strip() if soup.title and soup.title.string else ""
    meta = soup.find("meta", attrs={"name": "description"})
    description = (meta.get("content") or "").strip() if meta else ""
    if not title or not description:
        missing = [n for n, v in (("title", title), ("meta description", description)) if not v]
        out.append(_finding(policy, "Q3", route, f"Page is missing {' and '.join(missing)}"))

    broken = []
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if not href.startswith("/") or href.startswith("//"):
            continue
        if href not in link_cache:
            probe = _fetch(client, base_url, href)
            link_cache[href] = probe.status if probe.ok else None
        status = link_cache[href]
        if status is None or status >= 400:
            broken.append(f"{href} -> {status if status else 'no response'}")
    if broken:
        out.append(_finding(policy, "Q4", route, f"{len(broken)} internal link(s) do not resolve: " + "; ".join(broken[:3])))

    return out


def check_performance(fetched: Fetched, policy: dict) -> list[dict]:
    limit = _threshold(policy, "response_time_ms", 500)
    if not fetched.ok or fetched.elapsed_ms <= limit:
        return []
    return [
        _finding(
            policy,
            "P1",
            fetched.route,
            f"Response took {fetched.elapsed_ms}ms, over the {limit}ms budget",
        )
    ]


# --------------------------------------------------------------------------
# the run
# --------------------------------------------------------------------------
def _app_route(routes: list[str]) -> str:
    """Where app-level findings land.

    S9, S11 and S12 are properties of the deployment, not of one page. They are
    attributed to the app root so each one is a single finding with a stable
    identity, rather than the same defect repeated against every page.
    """
    return "/" if "/" in routes else (routes[0] if routes else "/")


def run_audit(base_url: str | None = None, routes: list[str] | None = None, policy_path: str | None = None) -> AuditResult:
    """Fetch every route, run all seventeen checks, grade each page.

    Never raises. A failing audit must not take down the scheduler that calls
    it every five minutes -- it reports what it saw, including that it saw
    nothing.
    """
    base_url = base_url or config.PULSE_BASE_URL
    routes = list(routes or ["/"])
    policy = load_policy(policy_path)
    app_route = _app_route(routes)

    started = time.perf_counter()
    findings: list[dict] = []
    pages: dict[str, str] = {}
    link_cache: dict[str, int | None] = {}
    reachable_any = False

    with telemetry.stage_span("forge.audit", "audit") as span:
        try:
            with httpx.Client(follow_redirects=False, timeout=FETCH_TIMEOUT) as client:
                for route in routes:
                    fetched = _fetch(client, base_url, route, origin=True)
                    pages[route] = fetched.body
                    reachable_any = reachable_any or fetched.has_content
                    if not fetched.ok:
                        log.warning("audit could not reach %s: %s", fetched.url, fetched.error)

                    for check in (check_headers, check_secrets, check_performance):
                        try:
                            findings.extend(check(fetched, policy))
                        except Exception as exc:
                            log.error("check %s crashed on %s: %s", check.__name__, route, exc)
                    try:
                        findings.extend(check_dom(fetched, policy, client, base_url, link_cache))
                    except Exception as exc:
                        log.error("check_dom crashed on %s: %s", route, exc)

                # app-level probes, once per run
                if reachable_any:
                    for probe in (probe_exposure, probe_stack_trace):
                        try:
                            findings.extend(probe(client, base_url, policy, app_route))
                        except Exception as exc:
                            log.error("probe %s crashed: %s", probe.__name__, exc)
        except Exception as exc:
            log.error("audit run failed: %s", exc)
            if span is not None:
                try:
                    span.record_exception(exc)
                except Exception:
                    pass

        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        grades = {r: grade_for([f for f in findings if f.get("route") == r]) for r in routes}
        result = AuditResult(
            base_url=base_url,
            routes_checked=routes,
            findings=findings,
            grades=grades,
            duration_ms=duration_ms,
            reachable=reachable_any,
            pages=pages,
        )

        if span is not None:
            span.set_attribute("audit.routes_checked", len(routes))
            span.set_attribute("audit.findings_total", len(findings))
            span.set_attribute("audit.findings_high", len(result.findings_high))
            span.set_attribute("audit.grade_worst", result.worst_grade)
            span.set_attribute("audit.reachable", reachable_any)
            span.set_attribute("audit.base_url", base_url)

        for finding in findings:
            telemetry.counter(
                "forge_findings_total",
                1,
                severity=finding["severity"],
                check_id=finding["check_id"],
                route=finding["route"],
            )
        telemetry.histogram("forge_audit_duration_ms", duration_ms, routes=len(routes))
        for route, grade in grades.items():
            telemetry.gauge("forge_security_grade", GRADE_VALUE.get(grade, 0), route=route)

        log.info(
            "audit of %s: %s findings (%s HIGH) across %s routes in %sms, worst grade %s",
            base_url,
            len(findings),
            len(result.findings_high),
            len(routes),
            duration_ms,
            result.worst_grade,
        )
        return result

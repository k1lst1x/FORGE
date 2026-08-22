"""
forge/portal.py -- the real Port client.

THE 404 THIS FIXES
--------------------------------------------------------------------------
Port runs two regions with two API hosts. app.port.io talks to api.port.io and
app.us.port.io talks to api.us.port.io -- and calling the wrong one does not
say "wrong region", it 404s or 401s as if your data were missing. These
credentials authenticate against api.port.io (EU); the US host rejects them.
So the host is DETECTED once by trying to authenticate, not guessed, and the
answer is cached for the process.

Everything degrades honestly: with no credentials the factory keeps running and
records locally, and says on /api/status that Port is not connected. It never
pretends a run reached the control plane when it did not.
"""
from __future__ import annotations

import logging
import threading
import time

import httpx

from forge import config, store

log = logging.getLogger("forge.portal")

STUB = False
HOSTS = ("https://api.port.io", "https://api.us.port.io")

_LOCK = threading.Lock()
_TOKEN: dict = {"value": None, "expires_at": 0.0, "host": None}

BLUEPRINT_RUN = "factory_run"
BLUEPRINT_PAGE = "page"
BLUEPRINT_FINDING = "finding"


def configured() -> bool:
    return bool(config.PORT_CLIENT_ID and config.PORT_CLIENT_SECRET)


def _authenticate(host: str) -> str | None:
    try:
        response = httpx.post(
            f"{host}/v1/auth/access_token",
            json={"clientId": config.PORT_CLIENT_ID, "clientSecret": config.PORT_CLIENT_SECRET},
            timeout=15,
        )
        if response.status_code == 200:
            return response.json().get("accessToken")
        log.debug("port auth against %s: %s", host, response.status_code)
    except Exception as exc:
        log.debug("port auth against %s failed: %s", host, exc)
    return None


def token() -> tuple[str, str] | tuple[None, None]:
    """A valid JWT and the host it belongs to. Cached, refreshed before expiry."""
    if not configured():
        return None, None
    with _LOCK:
        if _TOKEN["value"] and time.time() < _TOKEN["expires_at"]:
            return _TOKEN["value"], _TOKEN["host"]

        hosts = [config.PORT_API_BASE] if config.PORT_API_BASE else list(HOSTS)
        for host in hosts:
            value = _authenticate(host)
            if value:
                _TOKEN.update({"value": value, "host": host, "expires_at": time.time() + 45 * 60})
                log.info("Port authenticated against %s", host)
                return value, host

        log.error(
            "Port authentication failed on every host (%s). Check PORT_CLIENT_ID/SECRET and the "
            "region -- app.port.io uses api.port.io, app.us.port.io uses api.us.port.io.",
            ", ".join(hosts),
        )
        return None, None


def _request(method: str, path: str, **kwargs) -> httpx.Response | None:
    value, host = token()
    if not value:
        return None
    try:
        response = httpx.request(
            method,
            f"{host}{path}",
            headers={"Authorization": f"Bearer {value}"},
            timeout=20,
            **kwargs,
        )
        if response.status_code == 404:
            log.warning(
                "Port returned 404 for %s %s. The blueprint probably does not exist yet -- "
                "run scripts/port_setup.py to create them.", method, path,
            )
        elif response.status_code >= 400:
            log.warning("Port %s %s -> %s %s", method, path, response.status_code, response.text[:200])
        return response
    except Exception as exc:
        log.warning("Port request %s %s failed: %s", method, path, exc)
        return None


def _upsert_entity(blueprint: str, identifier: str, title: str, properties: dict) -> str | None:
    response = _request(
        "POST",
        f"/v1/blueprints/{blueprint}/entities?upsert=true&merge=true",
        json={"identifier": identifier, "title": title, "properties": properties},
    )
    if response is not None and response.status_code < 300:
        return identifier
    return None


# --------------------------------------------------------------------------
# the frozen surface the engine calls
# --------------------------------------------------------------------------
def upsert_run(cr) -> str:
    """Called on every step transition, so the run animates in Port's UI."""
    store.record_run(cr)  # always recorded locally, Port or no Port
    summary = cr.summary()
    _upsert_entity(
        BLUEPRINT_RUN,
        cr.run_id,
        cr.title[:120],
        {
            "intake": summary["intake"],
            "stage": summary["stage"],
            "status": summary["status"],
            "trace_id": summary.get("trace_id") or "",
            "classification": summary.get("classification") or "",
            "should_act": bool(summary.get("should_act")),
            "pr_url": summary.get("pr_url") or "",
            "files_changed": summary.get("files_changed") or [],
            "tests_passed": bool((cr.verify or {}).get("tests_passed")),
            "duration_ms": summary.get("duration_ms") or 0,
            "justification": (summary.get("justification") or "")[:900],
        },
    )
    return cr.run_id


def update_scorecard(route: str, grade: str, findings: list) -> None:
    """Push the numbers Port's scorecard turns into a grade."""
    highs = len([f for f in findings or [] if (f.get("severity") or "").upper() == "HIGH"])
    meds = len([f for f in findings or [] if (f.get("severity") or "").upper() == "MED"])
    _upsert_entity(
        BLUEPRINT_PAGE,
        route,
        route,
        {
            "route": route,
            "grade": grade,
            "open_findings_high": highs,
            "open_findings_med": meds,
            "open_findings": len(findings or []),
            "last_audited": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    )


def request_approval(cr) -> str:
    """Ask a human. The approval lives in Port when it is reachable, and in the
    factory's own queue either way -- so the gate is never skipped."""
    approval_id = f"approval_{cr.run_id}"
    _upsert_entity(
        BLUEPRINT_RUN, cr.run_id, cr.title[:120],
        {"status": "awaiting_approval", "stage": "GATE", "pr_url": cr.pr_url or ""},
    )
    from forge import approvals

    approvals.open_request(cr, approval_id)
    return approval_id


def wait_for_approval(approval_id: str) -> bool:
    from forge import approvals

    return approvals.wait(approval_id)


def escalate(cr, reason: str) -> str:
    """An escalation must look different from an approval in Port: different
    status, nothing to approve, because there is nothing to approve."""
    escalation_id = f"escalation_{cr.run_id}"
    _upsert_entity(
        BLUEPRINT_RUN, cr.run_id, cr.title[:120],
        {
            "status": "escalated",
            "stage": cr.stage,
            "should_act": False,
            "classification": cr.classification or "",
            "justification": reason[:900],
        },
    )
    return escalation_id

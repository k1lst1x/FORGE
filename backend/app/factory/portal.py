from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from app.core.config import settings
from app.factory.models import ChangeRequest

logger = logging.getLogger(__name__)

_PORT_BLUEPRINT = "forge_run"
_ACCESS_TOKEN: str | None = None
_TOKEN_REFRESHED_AT = 0.0
_ENTITIES_PUSHED = 0
_LAST_ERROR: str | None = None


def _normalize_port_base_url(value: str | None) -> str:
    base = (value or settings.port_api_base or settings.port_base_url or "https://api.getport.io").rstrip("/")
    if base.endswith("/v1"):
        return base
    return f"{base}/v1"


def _port_base_url() -> str:
    return _normalize_port_base_url(settings.port_api_base or settings.port_base_url)


def _set_last_error(message: str | None) -> None:
    global _LAST_ERROR
    _LAST_ERROR = message
    if message:
        logger.error("Port API error: %s", message)


def _port_headers() -> dict[str, str]:
    token = _get_access_token()
    if not token:
        return {"Content-Type": "application/json"}
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _get_access_token() -> str:
    global _ACCESS_TOKEN, _TOKEN_REFRESHED_AT
    if not settings.port_client_id or not settings.port_client_secret:
        raise ValueError("Port credentials are not configured.")

    now = time.time()
    if _ACCESS_TOKEN and now - _TOKEN_REFRESHED_AT < 3500:
        return _ACCESS_TOKEN

    try:
        response = httpx.post(
            f"{_port_base_url()}/auth/access_token",
            json={
                "clientId": settings.port_client_id,
                "clientSecret": settings.port_client_secret,
            },
            timeout=20.0,
        )
        if response.status_code >= 400:
            body = response.text[:500]
            _set_last_error(f"status={response.status_code} body={body}")
            return ""
        payload = response.json()
        token = payload.get("accessToken")
        if not token:
            _set_last_error("Port auth response missing accessToken.")
            return ""
        _ACCESS_TOKEN = str(token)
        _TOKEN_REFRESHED_AT = now
        _set_last_error(None)
        return _ACCESS_TOKEN
    except (httpx.HTTPError, ValueError, RuntimeError, TypeError) as exc:
        _set_last_error(str(exc))
        return ""


def _blueprint_schema() -> dict[str, Any]:
    return {
        "properties": {
            "intake": {"type": "string", "title": "Intake"},
            "stage": {"type": "string", "title": "Stage"},
            "status": {"type": "string", "title": "Status"},
            "trace_id": {"type": "string", "title": "Trace ID"},
            "classification": {"type": "string", "title": "Classification"},
            "should_act": {"type": "boolean", "title": "Should act"},
            "justification": {"type": "string", "title": "Justification"},
            "route": {"type": "string", "title": "Route"},
            "check_id": {"type": "string", "title": "Check ID"},
            "attempts": {"type": "number", "title": "Attempts"},
            "files_changed": {"type": "array", "title": "Files changed"},
            "branch": {"type": "string", "title": "Branch"},
            "pr_url": {"type": "string", "title": "PR URL", "format": "url"},
            "approved": {"type": "boolean", "title": "Approved"},
            "outcome": {"type": "string", "title": "Outcome"},
            "duration_ms": {"type": "number", "title": "Duration (ms)"},
        }
    }


def ensure_blueprint() -> dict[str, object]:
    if not settings.port_client_id or not settings.port_client_secret:
        return {"status": "skipped", "reason": "missing credentials"}

    try:
        headers = _port_headers()
        if "Authorization" not in headers:
            return {"status": "skipped", "reason": "token unavailable"}

        blueprint = {
            "identifier": _PORT_BLUEPRINT,
            "title": "Factory Run",
            "icon": "Build",
            "schema": _blueprint_schema(),
        }

        get_response = httpx.get(
            f"{_port_base_url()}/blueprints/{_PORT_BLUEPRINT}",
            headers=headers,
            timeout=20.0,
        )
        if get_response.status_code == 200:
            return get_response.json()

        create_response = httpx.post(
            f"{_port_base_url()}/blueprints",
            headers=headers,
            json=blueprint,
            timeout=20.0,
        )
        if create_response.status_code >= 400:
            body = create_response.text[:500]
            _set_last_error(f"status={create_response.status_code} body={body}")
            return {"status": "skipped", "reason": "blueprint creation failed"}
        return create_response.json()
    except (httpx.HTTPError, ValueError, RuntimeError, TypeError) as exc:
        _set_last_error(str(exc))
        return {"status": "skipped", "reason": "port api unavailable"}


def port_health() -> dict[str, object]:
    configured = bool(settings.port_client_id and settings.port_client_secret)
    auth_ok = configured and bool(_get_access_token())
    return {
        "configured": configured,
        "auth_ok": auth_ok,
        "entities_pushed": _ENTITIES_PUSHED,
        "last_error": _LAST_ERROR,
    }


def backfill_runs() -> int:
    from app.factory import store

    if not settings.port_client_id or not settings.port_client_secret:
        return 0

    pushed = 0
    for run in store.list_runs():
        upsert_run(run)
        pushed += 1
    return pushed


def upsert_run(cr: ChangeRequest) -> str:
    global _ENTITIES_PUSHED
    if not settings.port_client_id or not settings.port_client_secret:
        return cr.run_id

    try:
        ensure_blueprint()
        headers = _port_headers()
        if "Authorization" not in headers:
            return cr.run_id

        payload = {
            "identifier": cr.run_id,
            "title": cr.title,
            "properties": {
                "intake": cr.intake.value if hasattr(cr.intake, "value") else str(cr.intake),
                "stage": getattr(cr, "stage", "planned"),
                "status": cr.outcome or "planned",
                "trace_id": cr.trace_id or "",
                "classification": getattr(cr, "classification", "") or "",
                "should_act": bool(getattr(cr, "should_act", True)),
                "justification": getattr(cr, "justification", "") or "",
                "route": getattr(cr, "route", "") or "",
                "check_id": getattr(cr, "check_id", "") or "",
                "attempts": int(getattr(cr, "attempts", 0) or 0),
                "files_changed": list(getattr(cr, "changeset", []) or []),
                "branch": cr.branch or "",
                "pr_url": cr.pr_url or "",
                "approved": bool(getattr(cr, "approved", False)),
                "outcome": cr.outcome or "",
                "duration_ms": float(getattr(cr, "duration_ms", 0.0) or 0.0),
            },
        }
        response = httpx.post(
            f"{_port_base_url()}/blueprints/{_PORT_BLUEPRINT}/entities",
            headers=headers,
            json=payload,
            timeout=20.0,
        )
        if response.status_code >= 400:
            body = response.text[:500]
            logger.error("Port upsert failed for %s: status=%s body=%s", cr.run_id, response.status_code, body)
            _set_last_error(f"status={response.status_code} body={body}")
            return cr.run_id
        _ENTITIES_PUSHED += 1
        _set_last_error(None)
        return response.json().get("entity", {}).get("identifier", cr.run_id)
    except (httpx.HTTPError, ValueError, RuntimeError, TypeError) as exc:
        logger.exception("Port upsert failed for %s", cr.run_id)
        _set_last_error(str(exc))
        return cr.run_id


def update_scorecard(route: str, grade: str, findings: list[dict]) -> None:
    _ = (route, grade, findings)


def request_approval(cr: ChangeRequest) -> str:
    return f"approval-{cr.run_id}"


def wait_for_approval(approval_id: str) -> bool:
    _ = approval_id
    return False


def escalate(cr: ChangeRequest, reason: str) -> str:
    _ = cr
    return f"escalation-{reason.lower().replace(' ', '-')[:48]}"

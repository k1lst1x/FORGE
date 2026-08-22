from __future__ import annotations

import httpx

from app.core.config import settings
from app.factory.models import ChangeRequest

_PORT_BLUEPRINT = "forge_run"


def _port_headers() -> dict[str, str]:
    token = _get_access_token()
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _get_access_token() -> str:
    if not settings.port_client_id or not settings.port_client_secret:
        raise ValueError("Port credentials are not configured.")

    try:
        response = httpx.post(
            f"{settings.port_base_url}/auth/access_token",
            json={
                "clientId": settings.port_client_id,
                "clientSecret": settings.port_client_secret,
            },
            timeout=20.0,
        )
        response.raise_for_status()
    except (httpx.HTTPError, RuntimeError, ValueError):
        return ""

    token = response.json().get("accessToken")
    if not token:
        return ""
    return token


def ensure_blueprint() -> dict[str, object]:
    if not settings.port_client_id or not settings.port_client_secret:
        return {"status": "skipped", "reason": "missing credentials"}

    try:
        headers = _port_headers()
        if not headers:
            return {"status": "skipped", "reason": "token unavailable"}

        get_response = httpx.get(
            f"{settings.port_base_url}/blueprints/{_PORT_BLUEPRINT}",
            headers=headers,
            timeout=20.0,
        )

        if get_response.status_code == 200:
            return get_response.json()

        blueprint = {
            "identifier": _PORT_BLUEPRINT,
            "title": "Forge Run",
            "icon": "Build",
            "schema": {
                "properties": {
                    "status": {"type": "string", "title": "Status"},
                    "trigger": {"type": "string", "title": "Trigger"},
                    "brief": {"type": "string", "title": "Brief"},
                    "branch": {"type": "string", "title": "Branch"},
                    "pr_url": {"type": "string", "title": "PR URL", "format": "url"},
                    "outcome": {"type": "string", "title": "Outcome"},
                    "trace_id": {"type": "string", "title": "Trace ID"},
                }
            },
        }
        create_response = httpx.post(
            f"{settings.port_base_url}/blueprints",
            headers=headers,
            json=blueprint,
            timeout=20.0,
        )
        if create_response.status_code >= 400:
            return {"status": "skipped", "reason": "blueprint creation failed"}
        return create_response.json()
    except (httpx.HTTPError, ValueError):
        return {"status": "skipped", "reason": "port api unavailable"}


def upsert_run(cr: ChangeRequest) -> str:
    if not settings.port_client_id or not settings.port_client_secret:
        return f"port-run-{cr.run_id}"

    try:
        ensure_blueprint()
        payload = {
            "identifier": cr.run_id,
            "title": cr.title,
            "properties": {
                "status": cr.outcome or "planned",
                "trigger": "manual",
                "brief": cr.brief_text or cr.title,
                "branch": cr.branch or "",
                "pr_url": cr.pr_url or "",
                "outcome": cr.outcome or "pending",
                "trace_id": cr.trace_id or "",
            },
        }
        response = httpx.post(
            f"{settings.port_base_url}/blueprints/{_PORT_BLUEPRINT}/entities",
            headers=_port_headers(),
            json=payload,
            timeout=20.0,
        )
        if response.status_code >= 400:
            return f"port-run-{cr.run_id}"
        return response.json().get("entity", {}).get("identifier", cr.run_id)
    except (httpx.HTTPError, ValueError, RuntimeError, TypeError):
        return f"port-run-{cr.run_id}"


def update_scorecard(route: str, grade: str, findings: list[dict]) -> None:
    from app.factory.port_catalog import update_page_scorecard

    update_page_scorecard(route, grade, findings)


def request_approval(cr: ChangeRequest) -> str:
    return f"approval-{cr.run_id}"


def wait_for_approval(approval_id: str) -> bool:
    _ = approval_id
    return False


def escalate(cr: ChangeRequest, reason: str) -> str:
    _ = cr
    return f"escalation-{reason.lower().replace(' ', '-')[:48]}"

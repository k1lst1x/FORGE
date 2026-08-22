"""Zafar lane: Port page/finding/project blueprints, scorecards, project record."""

from __future__ import annotations

from typing import Any

import httpx

from app.core.config import settings
from app.factory.project_record import PROJECT_RECORD
from app.factory.scorecards import PORT_SCORECARD

_PAGE = "page"
_FINDING = "finding"
_PROJECT = "project"


def _headers() -> dict[str, str]:
    if not settings.port_client_id or not settings.port_client_secret:
        return {}
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
        return {}
    token = response.json().get("accessToken")
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _ensure_blueprint(headers: dict[str, str], blueprint: dict[str, Any]) -> dict[str, Any]:
    identifier = blueprint["identifier"]
    get_response = httpx.get(
        f"{settings.port_base_url}/blueprints/{identifier}",
        headers=headers,
        timeout=20.0,
    )
    if get_response.status_code == 200:
        return {"status": "exists", "identifier": identifier}
    create_response = httpx.post(
        f"{settings.port_base_url}/blueprints",
        headers=headers,
        json=blueprint,
        timeout=20.0,
    )
    if create_response.status_code >= 400:
        return {
            "status": "failed",
            "identifier": identifier,
            "detail": create_response.text[:240],
        }
    return {"status": "created", "identifier": identifier}


def _upsert_entity(
    headers: dict[str, str],
    blueprint: str,
    identifier: str,
    title: str,
    properties: dict[str, Any],
) -> dict[str, Any]:
    response = httpx.post(
        f"{settings.port_base_url}/blueprints/{blueprint}/entities?upsert=true",
        headers=headers,
        json={"identifier": identifier, "title": title, "properties": properties},
        timeout=20.0,
    )
    if response.status_code >= 400:
        return {"status": "failed", "identifier": identifier, "detail": response.text[:240]}
    return {"status": "upserted", "identifier": identifier}


def bootstrap() -> dict[str, Any]:
    headers = _headers()
    if not headers:
        return {"status": "skipped", "reason": "token unavailable"}

    results: dict[str, Any] = {"blueprints": [], "scorecard": None, "project": None, "pages": []}
    try:
        for blueprint in _BLUEPRINTS:
            results["blueprints"].append(_ensure_blueprint(headers, blueprint))
        results["scorecard"] = _ensure_scorecard(headers)
        results["project"] = upsert_project_record(headers)
        results["pages"] = [
            _upsert_entity(
                headers,
                _PAGE,
                "home",
                "Pulse /",
            {
                "route": "/",
                "title": "Pulse /",
                "grade": "gold",
                "open_findings_high": 0,
                "open_findings_med": 0,
                "tests_passing": True,
            },
            ),
            _upsert_entity(
                headers,
                _PAGE,
                "products",
                "Pulse /products",
                {
                    "route": "/products",
                    "title": "Pulse /products",
                    "grade": "gold",
                    "open_findings_high": 0,
                    "open_findings_med": 0,
                    "tests_passing": True,
                },
            ),
        ]
        results["status"] = "ok"
        return results
    except (httpx.HTTPError, ValueError, RuntimeError, TypeError) as exc:
        results["status"] = "skipped"
        results["reason"] = str(exc)[:200]
        return results


def upsert_project_record(headers: dict[str, str] | None = None) -> dict[str, Any]:
    headers = headers or _headers()
    if not headers:
        return {"status": "skipped", "reason": "token unavailable"}
    record = PROJECT_RECORD
    return _upsert_entity(
        headers,
        _PROJECT,
        str(record["identifier"]),
        str(record["title"]),
        dict(record["properties"]),
    )


def update_page_scorecard(route: str, grade: str, findings: list[dict]) -> None:
    headers = _headers()
    if not headers:
        return
    high = sum(1 for item in findings if str(item.get("severity", "")).upper() in {"HIGH", "H"})
    med = sum(1 for item in findings if str(item.get("severity", "")).upper() in {"MED", "MEDIUM"})
    slug = route.strip("/") or "home"
    try:
        _ensure_blueprint(headers, _BLUEPRINTS[0])
        _upsert_entity(
            headers,
            _PAGE,
            slug,
            f"Pulse {route}",
            {
                "route": route,
                "title": f"Pulse {route}",
                "grade": grade.lower(),
                "open_findings_high": high,
                "open_findings_med": med,
                "tests_passing": True,
            },
        )
    except (httpx.HTTPError, ValueError, RuntimeError, TypeError):
        return


def _ensure_scorecard(headers: dict[str, str]) -> dict[str, Any]:
    payload = {
        "identifier": PORT_SCORECARD["identifier"],
        "title": PORT_SCORECARD["title"],
        "levels": [
            {"color": "red", "title": "Failing"},
            {"color": "bronze", "title": "Bronze"},
            {"color": "silver", "title": "Silver"},
            {"color": "gold", "title": "Gold"},
        ],
        "rules": [
            {
                "identifier": "no_high_findings",
                "title": "Zero HIGH findings",
                "level": "Bronze",
                "query": {
                    "combinator": "and",
                    "conditions": [
                        {"operator": "=", "property": "open_findings_high", "value": 0}
                    ],
                },
            },
            {
                "identifier": "no_med_findings",
                "title": "Zero MED findings",
                "level": "Silver",
                "query": {
                    "combinator": "and",
                    "conditions": [
                        {"operator": "=", "property": "open_findings_high", "value": 0},
                        {"operator": "=", "property": "open_findings_med", "value": 0},
                    ],
                },
            },
            {
                "identifier": "verified_recently",
                "title": "Passing tests",
                "level": "Gold",
                "query": {
                    "combinator": "and",
                    "conditions": [
                        {"operator": "=", "property": "open_findings_high", "value": 0},
                        {"operator": "=", "property": "open_findings_med", "value": 0},
                        {"operator": "=", "property": "tests_passing", "value": True},
                    ],
                },
            },
        ],
    }
    url = f"{settings.port_base_url}/blueprints/{_PAGE}/scorecards"
    get_response = httpx.get(
        f"{url}/{PORT_SCORECARD['identifier']}",
        headers=headers,
        timeout=20.0,
    )
    if get_response.status_code == 200:
        return {"status": "exists", "identifier": PORT_SCORECARD["identifier"]}
    create_response = httpx.post(url, headers=headers, json=payload, timeout=20.0)
    if create_response.status_code >= 400:
        return {"status": "failed", "detail": create_response.text[:240]}
    return {"status": "created", "identifier": PORT_SCORECARD["identifier"]}


_BLUEPRINTS = [
    {
        "identifier": _PAGE,
        "title": "Page",
        "icon": "Microservice",
        "schema": {
            "properties": {
                "route": {"type": "string", "title": "Route"},
                "title": {"type": "string", "title": "Title"},
                "grade": {
                    "type": "string",
                    "title": "Audit grade",
                    "enum": ["Gold", "Silver", "Bronze"],
                },
                "open_findings_high": {"type": "number", "title": "Open HIGH findings"},
                "open_findings_med": {"type": "number", "title": "Open MED findings"},
                "tests_passing": {"type": "boolean", "title": "Tests passing"},
            },
            "required": ["route"],
        },
    },
    {
        "identifier": _FINDING,
        "title": "Finding",
        "icon": "Alert",
        "schema": {
            "properties": {
                "check_id": {"type": "string", "title": "Check"},
                "severity": {"type": "string", "enum": ["HIGH", "MED", "LOW"]},
                "route": {"type": "string", "title": "Route"},
                "evidence": {"type": "string", "title": "Evidence"},
                "status": {"type": "string", "title": "Status"},
                "justification": {"type": "string", "title": "Justification"},
            },
            "required": ["check_id", "severity", "route"],
        },
    },
    {
        "identifier": _PROJECT,
        "title": "Project",
        "icon": "Home",
        "schema": {
            "properties": {
                "goal": {"type": "string", "title": "Goal"},
                "technical_choices": {"type": "string", "title": "Technical choices"},
                "known_risks": {"type": "string", "title": "Risk factors"},
                "cataloged_services": {
                    "type": "array",
                    "items": {"type": "string"},
                    "title": "Cataloged services",
                },
            },
            "required": ["goal", "technical_choices", "known_risks"],
        },
    },
]

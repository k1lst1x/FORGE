from __future__ import annotations

import asyncio
from typing import Any

import httpx
import websockets

from app.core.config import settings


def _service_state(service: str, *, ok: bool, message: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"service": service, "ok": ok, "message": message}
    payload.update(extra)
    return payload


def check_port() -> dict[str, Any]:
    if not settings.port_client_id or not settings.port_client_secret:
        return _service_state(
            "port",
            ok=False,
            message="Missing Port client credentials in FORGE_PORT_CLIENT_ID / FORGE_PORT_CLIENT_SECRET.",
        )

    try:
        response = httpx.post(
            f"{settings.port_base_url}/auth/access_token",
            json={
                "clientId": settings.port_client_id,
                "clientSecret": settings.port_client_secret,
            },
            timeout=20.0,
        )
    except httpx.HTTPError as exc:
        return _service_state("port", ok=False, message=f"HTTP error: {exc}")

    if response.status_code != 200:
        return _service_state(
            "port",
            ok=False,
            message="Port token request failed.",
            status_code=response.status_code,
            detail=response.text[:200],
        )

    data = response.json()
    token = data.get("accessToken")
    return _service_state(
        "port",
        ok=bool(token),
        message="Port token generated successfully." if token else "Port responded without an access token.",
        status_code=response.status_code,
    )


def check_openai() -> dict[str, Any]:
    if not settings.openai_api_key:
        return _service_state(
            "openai",
            ok=False,
            message="Missing OpenAI API key in FORGE_OPENAI_API_KEY.",
        )

    try:
        response = httpx.get(
            f"{settings.openai_api_base_url}/models",
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            timeout=20.0,
        )
    except httpx.HTTPError as exc:
        return _service_state("openai", ok=False, message=f"HTTP error: {exc}")

    if response.status_code != 200:
        return _service_state(
            "openai",
            ok=False,
            message="OpenAI models request failed.",
            status_code=response.status_code,
            detail=response.text[:200],
        )

    return _service_state(
        "openai",
        ok=True,
        message="OpenAI API connectivity confirmation received.",
        status_code=response.status_code,
    )


async def check_brightdata_browser() -> dict[str, Any]:
    if not settings.brightdata_browser_ws_url:
        return _service_state(
            "brightdata_browser",
            ok=False,
            message="Missing Bright Data browser websocket URL in FORGE_BRIGHTDATA_BROWSER_WS_URL.",
        )

    try:
        async with websockets.connect(
            settings.brightdata_browser_ws_url,
            open_timeout=15,
            ping_interval=None,
            proxy=True,
        ) as websocket:
            await websocket.ping()
            return _service_state(
                "brightdata_browser",
                ok=True,
                message="Bright Data browser websocket handshake succeeded.",
            )
    except Exception as exc:  # pragma: no cover - depends on network availability
        return _service_state("brightdata_browser", ok=False, message=f"WebSocket error: {type(exc).__name__}")


def check_brightdata_selenium() -> dict[str, Any]:
    if not settings.brightdata_selenium_url:
        return _service_state(
            "brightdata_selenium",
            ok=False,
            message="Missing Bright Data Selenium URL in FORGE_BRIGHTDATA_SELENIUM_URL.",
        )

    try:
        response = httpx.get(settings.brightdata_selenium_url, timeout=20.0)
    except httpx.HTTPError as exc:
        return _service_state("brightdata_selenium", ok=False, message=f"HTTP error: {exc}")

    if response.status_code in {200, 401, 403, 404}:
        return _service_state(
            "brightdata_selenium",
            ok=True,
            message="Bright Data Selenium endpoint is reachable and responded to the request.",
            status_code=response.status_code,
        )

    return _service_state(
        "brightdata_selenium",
        ok=False,
        message="Bright Data Selenium endpoint did not return a healthy response.",
        status_code=response.status_code,
    )


def check_signoz() -> dict[str, Any]:
    if not settings.signoz_ingestion_key:
        return _service_state(
            "signoz",
            ok=False,
            message="Missing SigNoz ingestion key in FORGE_SIGNOZ_INGESTION_KEY.",
        )

    if not settings.signoz_ingest_base_url:
        return _service_state(
            "signoz",
            ok=True,
            message="SigNoz key is configured; add FORGE_SIGNOZ_INGEST_BASE_URL to enable live endpoint verification.",
            configured=True,
        )

    try:
        response = httpx.get(
            settings.signoz_ingest_base_url,
            headers={"Authorization": f"signoz-ingestion-key {settings.signoz_ingestion_key}"},
            timeout=20.0,
        )
    except httpx.HTTPError as exc:
        return _service_state("signoz", ok=False, message=f"HTTP error: {exc}")

    return _service_state(
        "signoz",
        ok=response.status_code in {200, 400, 401, 403},
        message="SigNoz ingest endpoint is reachable." if response.status_code in {200, 400, 401, 403} else "SigNoz endpoint returned an unexpected response.",
        status_code=response.status_code,
    )


def smoke_checks() -> dict[str, Any]:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    browser = loop.run_until_complete(check_brightdata_browser())
    loop.close()
    return {
        "status": "ok" if all(item["ok"] for item in (check_port(), check_openai(), browser, check_brightdata_selenium(), check_signoz())) else "partial",
        "results": [
            check_port(),
            check_openai(),
            browser,
            check_brightdata_selenium(),
            check_signoz(),
        ],
    }

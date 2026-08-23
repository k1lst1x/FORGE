import asyncio

from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import Response

from pulse import main
from pulse.routes import security


def test_security_page_has_app_wide_security_headers(monkeypatch):
    monkeypatch.setattr(
        security,
        "_fetch",
        lambda: {"audited": False, "totals": {}, "routes": [], "findings": []},
    )

    response = TestClient(main.app).get("/security")

    assert response.status_code == 200
    assert response.headers["content-security-policy"] == "default-src 'self'"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["strict-transport-security"] == "max-age=31536000; includeSubDomains"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_security_headers_remove_server_and_powered_by_headers():
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})

    async def call_next(_request):
        return Response(headers={"Server": "uvicorn", "X-Powered-By": "framework"})

    response = asyncio.run(main.security_headers(request, call_next))

    assert "server" not in response.headers
    assert "x-powered-by" not in response.headers
    assert response.headers["x-frame-options"] == "DENY"

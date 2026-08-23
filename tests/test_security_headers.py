import asyncio

from fastapi import Request
from fastapi.responses import Response
from fastapi.testclient import TestClient

from pulse import main


def test_root_has_all_security_headers(monkeypatch):
    monkeypatch.setattr(
        main,
        "_feed",
        lambda: {
            "rows": [],
            "age_seconds": None,
            "source": None,
            "has_data": False,
            "last_success_at": None,
        },
    )

    response = TestClient(main.app).get("/")

    assert response.status_code == 200
    assert response.headers["content-security-policy"] == "default-src 'self'"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["strict-transport-security"] == "max-age=31536000; includeSubDomains"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_security_headers_remove_version_bearing_headers():
    response = Response()
    response.headers["Server"] = "uvicorn/1.0"
    response.headers["X-Powered-By"] = "example"
    scope = {"type": "http", "method": "GET", "path": "/", "headers": []}

    async def call_next(request):
        return response

    result = asyncio.run(main.security_headers(Request(scope), call_next))

    assert "server" not in result.headers
    assert "x-powered-by" not in result.headers

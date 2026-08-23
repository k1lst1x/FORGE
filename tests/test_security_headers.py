import asyncio

from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import Response

from pulse import main


def test_products_response_has_security_headers(monkeypatch):
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

    response = TestClient(main.app).get("/products")

    assert response.status_code == 200
    assert response.headers["content-security-policy"] == (
        "default-src 'self'; frame-ancestors 'none'"
    )
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["strict-transport-security"] == (
        "max-age=31536000; includeSubDomains"
    )
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_security_headers_removes_identifying_headers():
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/products",
            "headers": [],
            "query_string": b"",
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
        }
    )

    async def call_next(_request):
        return Response(headers={"Server": "uvicorn", "X-Powered-By": "FastAPI"})

    response = asyncio.run(main.security_headers(request, call_next))

    assert "server" not in response.headers
    assert "x-powered-by" not in response.headers

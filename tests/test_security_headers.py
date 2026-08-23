from fastapi.responses import Response
from fastapi.testclient import TestClient

from pulse import main


EXPECTED_HEADERS = {
    "content-security-policy": "default-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'self'",
    "x-frame-options": "DENY",
    "strict-transport-security": "max-age=31536000; includeSubDomains",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
}


def test_security_headers_apply_to_homepage_and_strip_leaked_headers(monkeypatch):
    monkeypatch.setattr(
        main,
        "_feed",
        lambda: {
            "rows": [],
            "age_seconds": None,
            "source": "https://example.com/products",
            "has_data": False,
            "last_success_at": None,
        },
    )

    def leaky_response():
        return Response(
            "ok",
            headers={"Server": "uvicorn/0.0", "X-Powered-By": "test-server"},
        )

    main.app.add_api_route("/_test_leaky_security_headers", leaky_response, methods=["GET"])
    client = TestClient(main.app)

    homepage = client.get("/")
    assert homepage.status_code == 200
    for header, value in EXPECTED_HEADERS.items():
        assert homepage.headers[header] == value

    response = client.get("/_test_leaky_security_headers")
    for header, value in EXPECTED_HEADERS.items():
        assert response.headers[header] == value
    assert "server" not in response.headers
    assert "x-powered-by" not in response.headers

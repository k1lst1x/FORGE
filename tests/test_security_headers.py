from fastapi.testclient import TestClient

from pulse.main import app


def test_root_response_has_security_headers():
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-security-policy"] == "default-src 'self'; frame-ancestors 'none'"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["strict-transport-security"] == "max-age=31536000; includeSubDomains"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "server" not in response.headers
    assert "x-powered-by" not in response.headers

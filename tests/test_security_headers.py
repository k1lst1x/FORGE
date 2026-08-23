from fastapi.testclient import TestClient

from pulse.main import app
from pulse.routes import security


def test_security_page_has_required_security_headers(monkeypatch):
    monkeypatch.setattr(
        security,
        "_fetch",
        lambda: {"audited": False, "totals": {}, "routes": [], "findings": []},
    )

    response = TestClient(app).get("/security")

    assert response.status_code == 200
    assert response.headers["content-security-policy"] == "default-src 'self'; frame-ancestors 'none'"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["strict-transport-security"] == "max-age=31536000; includeSubDomains"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "server" not in response.headers
    assert "x-powered-by" not in response.headers

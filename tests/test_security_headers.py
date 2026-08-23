from fastapi.testclient import TestClient

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
    assert "server" not in response.headers
    assert "x-powered-by" not in response.headers

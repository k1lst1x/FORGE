from fastapi.testclient import TestClient

from pulse.main import app


client = TestClient(app)


def test_pricing_page_shows_plans_toggle_and_comparison():
    response = client.get("/pricing")

    assert response.status_code == 200
    assert "Starter" in response.text
    assert "Growth" in response.text
    assert "Scale" in response.text
    assert "$19" in response.text
    assert "$190" in response.text
    assert 'id="monthly"' in response.text
    assert 'id="yearly"' in response.text
    assert "Compare plans" in response.text
    assert "Products tracked" in response.text


def test_security_headers_apply_to_all_pages():
    for path in ("/", "/products", "/pricing"):
        response = client.get(path)

        assert response.headers["content-security-policy"].startswith("default-src 'self'")
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["strict-transport-security"].startswith("max-age=")
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "no-referrer"


def test_sensitive_path_is_not_reachable():
    response = client.get("/.env")

    assert response.status_code == 404

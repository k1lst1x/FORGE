from fastapi.testclient import TestClient

from app.auth import require_auth
from app.main import app


def test_login_returns_bearer_token():
    response = TestClient(app).post(
        "/auth/login",
        json={"username": "admin", "password": "forge-local"},
    )

    assert response.status_code == 200
    assert response.json()["token_type"] == "bearer"
    assert response.json()["access_token"]


def test_login_rejects_invalid_password():
    response = TestClient(app).post(
        "/auth/login",
        json={"username": "admin", "password": "wrong-password"},
    )

    assert response.status_code == 401


def test_factory_requires_authentication():
    app.dependency_overrides.pop(require_auth, None)
    response = TestClient(app).get("/factory/runs")

    assert response.status_code == 401
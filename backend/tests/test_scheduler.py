from fastapi.testclient import TestClient

from app.main import app


def test_scheduler_status() -> None:
    with TestClient(app) as client:
        response = client.get("/factory/audit/status")

    assert response.status_code == 200
    assert response.json()["running"] is False

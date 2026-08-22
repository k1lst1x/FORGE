from fastapi.testclient import TestClient

from app.main import app


def test_create_factory_run() -> None:
    client = TestClient(app)

    response = client.post(
        "/factory/runs",
        json={"brief": "Build the factory that builds and repairs the app."},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "awaiting_human"
    assert body["steps"][-1]["name"] == "release"
    assert body["findings"][0]["check_id"] == "S1"

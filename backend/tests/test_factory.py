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


def test_approve_run() -> None:
    client = TestClient(app)

    created = client.post(
        "/factory/runs",
        json={"brief": "Approve this human gate path."},
    )
    run_id = created.json()["id"]

    response = client.post(f"/factory/runs/{run_id}/approve")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "released"
    assert body["outcome"] == "approved_by_human"


def test_reject_run() -> None:
    client = TestClient(app)

    created = client.post(
        "/factory/runs",
        json={"brief": "Reject this human gate path."},
    )
    run_id = created.json()["id"]

    response = client.post(f"/factory/runs/{run_id}/reject")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "escalated"
    assert body["outcome"] == "rejected_by_human"

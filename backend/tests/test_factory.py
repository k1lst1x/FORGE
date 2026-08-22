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


def test_create_planned_factory_run() -> None:
    client = TestClient(app)

    response = client.post(
        "/factory/runs",
        json={"brief": "Create a planned run for later execution.", "auto_start": False},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "planned"
    assert body["steps"] == []


def test_get_missing_run_is_404() -> None:
    client = TestClient(app)

    response = client.get("/factory/runs/does-not-exist")

    assert response.status_code == 404
    assert response.json()["detail"] == "Factory run not found"


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


def test_health_integrations_aggregation(monkeypatch) -> None:
    import app.factory.integrations as integrations

    async def fake_browser() -> dict[str, object]:
        return {"service": "brightdata_browser", "ok": True, "message": "ok"}

    monkeypatch.setattr(integrations, "check_port", lambda: {"service": "port", "ok": True, "message": "ok"})
    monkeypatch.setattr(integrations, "check_openai", lambda: {"service": "openai", "ok": True, "message": "ok"})
    monkeypatch.setattr(integrations, "check_brightdata_browser", fake_browser)
    monkeypatch.setattr(integrations, "check_brightdata_selenium", lambda: {"service": "brightdata_selenium", "ok": True, "message": "ok"})
    monkeypatch.setattr(integrations, "check_signoz", lambda: {"service": "signoz", "ok": True, "message": "ok"})

    response = TestClient(app).get("/health/integrations")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert len(body["results"]) == 5
    assert all(item["ok"] for item in body["results"])


def test_scheduler_start_stop_cycle() -> None:
    client = TestClient(app)

    started = client.post("/factory/audit/start")
    assert started.status_code == 200
    assert started.json()["running"] is True

    stopped = client.post("/factory/audit/stop")
    assert stopped.status_code == 200
    assert stopped.json()["running"] is False

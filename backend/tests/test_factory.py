from fastapi.testclient import TestClient

from app.core.config import settings
from app.factory import portal
from app.factory.models import ChangeRequest, IntakeType
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


def test_port_upsert_run_uses_real_entity_payload(monkeypatch) -> None:
    monkeypatch.setattr(settings, "port_client_id", "fake-client")
    monkeypatch.setattr(settings, "port_client_secret", "fake-secret")
    calls: list[tuple[str, str, dict]] = []

    class FakeResponse:
        def __init__(self, payload: dict, status_code: int = 200) -> None:
            self.payload = payload
            self.status_code = status_code

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP {self.status_code}")

        def json(self) -> dict:
            return self.payload

    def fake_get(url: str, **_: object) -> FakeResponse:
        calls.append(("GET", url, {}))
        return FakeResponse({"ok": True}, status_code=404)

    def fake_post(url: str, **kwargs: object) -> FakeResponse:
        calls.append(("POST", url, kwargs.get("json", {})))
        if url.endswith("/auth/access_token"):
            return FakeResponse({"accessToken": "fake-token"})
        if url.endswith("/blueprints"):
            return FakeResponse({"ok": True, "blueprint": {"identifier": "forge_run"}})
        return FakeResponse({"ok": True, "entity": {"identifier": "run_123"}})

    monkeypatch.setattr("app.factory.portal.httpx.get", fake_get)
    monkeypatch.setattr("app.factory.portal.httpx.post", fake_post)

    cr = ChangeRequest(
        run_id="run_123",
        intake=IntakeType.brief,
        title="Run payload test",
        brief_text="Verify Port sync payload",
        trace_id="trace-123",
        branch="forge/run_123",
        pr_url="https://example.com/pr/1",
        outcome="awaiting_human",
    )

    result = portal.upsert_run(cr)

    assert result == "run_123"
    assert any(call[1].endswith("/blueprints/forge_run/entities") for call in calls)
    entity_payload = next(payload for method, url, payload in calls if url.endswith("/blueprints/forge_run/entities"))
    assert entity_payload["properties"]["status"] == "awaiting_human"
    assert entity_payload["properties"]["trace_id"] == "trace-123"

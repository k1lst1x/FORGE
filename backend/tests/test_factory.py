from fastapi.testclient import TestClient

from app.core.config import settings
from app.factory import brightdata, portal, telemetry
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


def test_brightdata_snapshot_detects_change(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(brightdata, "_SNAPSHOT_DIR", tmp_path)
    responses = iter(["<html>first</html>", "<html>second</html>", "<html>third</html>"])

    class FakeResponse:
        def __init__(self, text: str) -> None:
            self.text = text

        def raise_for_status(self) -> None:
            return None

    def fake_get(url: str, **_: object) -> FakeResponse:
        return FakeResponse(next(responses))

    monkeypatch.setattr(brightdata.httpx, "get", fake_get)

    first = brightdata.snapshot_page("https://example.com/products")
    second = brightdata.snapshot_page("https://example.com/products")

    assert first["changed"] is True
    assert second["changed"] is True
    assert brightdata.detect_change("https://example.com/products") is True


def test_stage_span_emits_signoz_payload(monkeypatch) -> None:
    calls: list[tuple[str, dict, str]] = []

    monkeypatch.setattr(settings, "signoz_ingestion_key", "test-key")
    monkeypatch.setattr(settings, "signoz_ingest_base_url", "https://ingest.example.com")

    def fake_post(url: str, *, json: dict, headers: dict, timeout: float) -> object:
        calls.append((url, json, headers["signoz-ingestion-key"]))
        return object()

    monkeypatch.setattr("app.factory.telemetry.httpx.post", fake_post)

    with telemetry.stage_span("context", "run_123", "trace_456"):
        pass

    assert calls
    assert calls[0][2] == "test-key"
    assert calls[0][1]["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["attributes"][0]["key"] == "run_id"


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


def test_create_factory_run_rejects_empty_brief() -> None:
    client = TestClient(app)

    response = client.post("/factory/runs", json={"brief": ""})

    assert response.status_code == 422


def test_list_runs_returns_created_runs() -> None:
    client = TestClient(app)

    first = client.post("/factory/runs", json={"brief": "First run for list coverage."})
    second = client.post("/factory/runs", json={"brief": "Second run for list coverage."})

    assert first.status_code == 201
    assert second.status_code == 201

    response = client.get("/factory/runs")
    assert response.status_code == 200
    body = response.json()
    assert len(body) >= 2
    assert {item["brief"] for item in body if item["brief"] in {"First run for list coverage.", "Second run for list coverage."}} == {
        "First run for list coverage.",
        "Second run for list coverage.",
    }


def test_integration_health_reports_partial_when_unconfigured(monkeypatch) -> None:
    import app.factory.integrations as integrations

    monkeypatch.setattr(settings, "port_client_id", "")
    monkeypatch.setattr(settings, "port_client_secret", "")
    monkeypatch.setattr(settings, "openai_api_key", "")
    monkeypatch.setattr(settings, "brightdata_browser_ws_url", "")
    monkeypatch.setattr(settings, "brightdata_selenium_url", "")
    monkeypatch.setattr(settings, "signoz_ingestion_key", "")

    payload = integrations.smoke_checks()

    assert payload["status"] == "partial"
    assert any(item["service"] == "port" and not item["ok"] for item in payload["results"])
    assert any(item["service"] == "openai" and not item["ok"] for item in payload["results"])

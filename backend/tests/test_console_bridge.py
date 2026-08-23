"""Contract tests for the operator-console bridge (app/api/console.py).

The console decides between live and demo data on ONE signal: whether
GET /api/status answers. Every other poll degrades quietly, so a regression
here shows up as "DEMO DATA -- forge-control not reachable" and nothing else.
That is easy to mistake for a credentials problem, which is why it is pinned.
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_console_bridge_routes_need_no_auth() -> None:
    """The console holds a Supabase JWT this backend cannot validate.

    If these ever move behind require_auth they will 401 on every poll and the
    console drops straight back to demo data.
    """
    for path in ("/api/status", "/api/runs/current", "/api/findings", "/api/runs", "/api/catalog"):
        assert client.get(path).status_code == 200, path


def test_status_carries_the_keys_normstatus_reads() -> None:
    payload = client.get("/api/status").json()

    assert payload["scheduler"] in ("healthy", "down")

    # None when the scheduler is stopped -- there is no next audit to count to.
    # Reporting the interval instead froze the header at a constant 1:00: the
    # console ticks locally but re-reads this every 3s, so it fell to 0:57 and
    # snapped back forever. mmss(null) renders "--:--".
    if payload["scheduler"] == "healthy":
        assert isinstance(payload["next_audit_seconds"], int)
    else:
        assert payload["next_audit_seconds"] is None
    assert isinstance(payload["runs_today"], int)
    assert set(payload["severity"]) == {"HIGH", "MED", "LOW"}
    assert isinstance(payload["grades"], dict)
    assert len(payload["runs_per_hour"]) == 12


def test_current_run_is_200_with_null_never_404() -> None:
    """An idle factory must be able to say it is idle.

    A client cannot tell a 404 meaning "nothing is running" from a failed fetch
    meaning "the backend is down", and the console treats both as demo-mode.
    """
    response = client.get("/api/runs/current")
    assert response.status_code == 200
    assert "run" in response.json()


def test_findings_and_runs_use_the_envelope_the_console_unwraps() -> None:
    assert isinstance(client.get("/api/findings").json()["findings"], list)
    assert isinstance(client.get("/api/runs").json()["runs"], list)
    assert isinstance(client.get("/api/catalog").json()["pages"], list)


def test_a_run_created_through_the_bridge_is_visible_to_the_bridge() -> None:
    created = client.post(
        "/api/brief",
        json={"title": "bridge test", "description": "Exercise the console intake path."},
    )
    assert created.status_code == 200
    run_id = created.json()["run_id"]

    listed = client.get("/api/runs").json()["runs"]
    assert any(r["run_id"] == run_id for r in listed)

    current = client.get("/api/runs/current").json()["run"]
    assert current is not None
    # normRun() reads these; a missing stage field collapses the pipeline view.
    assert current["stage"] in (
        "INTAKE", "CONTEXT", "TRIAGE", "PLAN", "ACT", "VERIFY", "GATE", "RELEASE",
    )
    assert set(current["stages"]) == {
        "INTAKE", "CONTEXT", "TRIAGE", "PLAN", "ACT", "VERIFY", "GATE", "RELEASE",
    }


def test_approval_decision_removes_the_run_from_the_queue() -> None:
    created = client.post(
        "/api/brief",
        json={"title": "approval test", "description": "Exercise the console approval path."},
    )
    run_id = created.json()["run_id"]
    approval_id = f"approval-{run_id}"

    pending = client.get("/api/approvals").json()["pending"]
    assert any(p["approval_id"] == approval_id for p in pending)

    assert client.post(f"/api/approvals/{approval_id}/approve").status_code == 200

    still_pending = client.get("/api/approvals").json()["pending"]
    assert not any(p["approval_id"] == approval_id for p in still_pending)


def test_unknown_decision_and_unknown_approval_are_rejected() -> None:
    assert client.post("/api/approvals/approval-nope/sideways").status_code == 400
    assert client.post("/api/approvals/approval-nope/approve").status_code == 404

"""
tests/test_api_current_run.py -- GET /api/runs/current.

The contract is narrow and the whole point is the absence of a 404: a console
cannot distinguish "nothing is happening" from "the backend is down" if idleness
is reported as an error, and it will render a dead factory as an idle one.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("forge.scheduler.loop", lambda: _never())
    # forge.api was never importable -- app/api.py is shadowed by the
    # app/api/ package. The module now lives at app/control.py.
    from forge.control import app

    with TestClient(app) as c:
        yield c


async def _never():
    import asyncio

    await asyncio.sleep(3600)


def _run(run_id="run_1", stage="PLAN", status="running"):
    return {"run_id": run_id, "stage": stage, "status": status, "intake": "finding",
            "title": "docs open", "classification": "AUTOFIX_SAFE"}


def test_idle_returns_200_with_a_null_run(client, monkeypatch):
    monkeypatch.setattr("forge.store.list_runs", lambda limit=50: [])
    response = client.get("/api/runs/current")
    assert response.status_code == 200, "idle must never be a 404"
    assert response.json() == {"run": None}


def test_an_active_run_is_returned_whole(client, monkeypatch):
    monkeypatch.setattr("forge.store.list_runs", lambda limit=50: [_run()])
    body = client.get("/api/runs/current").json()
    assert body["run"]["run_id"] == "run_1"
    assert body["run"]["stage"] == "PLAN"


@pytest.mark.parametrize(
    "stage,status",
    [("AUDIT", "done"), ("AUDIT", "running"), ("GATE", "escalated"),
     ("VERIFY", "failed"), ("GATE", "rejected"), ("RELEASE", "done")],
)
def test_a_finished_run_is_not_current(client, monkeypatch, stage, status):
    monkeypatch.setattr("forge.store.list_runs", lambda limit=50: [_run(stage=stage, status=status)])
    assert client.get("/api/runs/current").json() == {"run": None}


@pytest.mark.parametrize("stage", ["INTAKE", "CONTEXT", "TRIAGE", "PLAN", "ACT", "VERIFY", "GATE", "RELEASE"])
def test_every_pre_audit_stage_counts_as_active(client, monkeypatch, stage):
    monkeypatch.setattr("forge.store.list_runs", lambda limit=50: [_run(stage=stage)])
    assert client.get("/api/runs/current").json()["run"]["stage"] == stage


def test_the_finished_run_is_skipped_for_the_live_one(client, monkeypatch):
    monkeypatch.setattr(
        "forge.store.list_runs",
        lambda limit=50: [_run("run_done", "AUDIT", "done"), _run("run_live", "VERIFY", "running")],
    )
    assert client.get("/api/runs/current").json()["run"]["run_id"] == "run_live"


def test_current_is_not_swallowed_by_the_run_id_route(client, monkeypatch):
    """Route order matters: /api/runs/{run_id} would match "current" and 404."""
    monkeypatch.setattr("forge.store.list_runs", lambda limit=50: [])
    monkeypatch.setattr("forge.store.get_run", lambda run_id: None)
    assert client.get("/api/runs/current").status_code == 200
    assert client.get("/api/runs/no_such_run").status_code == 404, "a real unknown id still 404s"


def test_the_shape_matches_run_detail(client, monkeypatch):
    run = _run()
    monkeypatch.setattr("forge.store.list_runs", lambda limit=50: [run])
    monkeypatch.setattr("forge.store.get_run", lambda run_id: run)
    assert client.get("/api/runs/current").json()["run"] == client.get("/api/runs/run_1").json()

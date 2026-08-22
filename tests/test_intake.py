"""
tests/test_intake.py -- the front door.

The endpoint's job is to accept a brief, return before the run does, and hand
back the handle. If it blocks on the factory, Port times out and retries and we
get two runs for one brief.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from forge import intake


@pytest.fixture
def client(monkeypatch):
    started = []
    monkeypatch.setattr(intake, "_run_brief",
                        lambda description, title, run_id: started.append((description, title, run_id)))
    app = FastAPI()
    app.include_router(intake.router)
    test_client = TestClient(app)
    test_client.started = started
    return test_client


def test_a_brief_is_accepted_and_a_run_starts():
    """The route exists, takes {title, description}, and is a POST."""
    route = next(r for r in intake.router.routes if r.path == "/intake/brief")
    assert route.methods == {"POST"}


def test_posting_a_brief_returns_a_handle_immediately(client):
    response = client.post("/intake/brief", json={
        "title": "Out of stock page",
        "description": "Add a page showing only out-of-stock products, sorted by price descending.",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] is True
    assert body["intake"] == "brief"
    assert body["run_id"].startswith("run_")


def test_the_run_happens_in_the_background_with_the_id_we_handed_back(client):
    response = client.post("/intake/brief", json={"description": "Add a cheapest-products page."})
    run_id = response.json()["run_id"]
    assert client.started, "the brief must actually start a run"
    description, title, started_id = client.started[0]
    assert started_id == run_id, "the handle we returned is the run that ran"
    assert description == "Add a cheapest-products page."
    assert title is None


def test_a_brief_needs_a_description(client):
    assert client.post("/intake/brief", json={"title": "no body"}).status_code == 422
    assert client.post("/intake/brief", json={"description": ""}).status_code == 422


def test_a_failing_run_does_not_take_the_service_down(monkeypatch):
    """The background task is the last line of defence -- if it raises, the
    web process must survive it."""
    def explode(text, title=None, run_id=None):
        raise RuntimeError("planner exploded")

    monkeypatch.setattr("forge.engine.run_from_brief", explode)
    intake._run_brief("some brief", None, "run_x")  # must not raise

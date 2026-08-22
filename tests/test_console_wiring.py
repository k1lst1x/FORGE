"""
tests/test_console_wiring.py -- the console calls endpoints that exist.

The console shipped calling /api/catalog, /api/brief and /api/runs/current --
none of which forge-control had. Every one failed silently into demo data, so
the screen looked healthy while showing invented numbers. This test fails the
build instead.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CONSOLE = Path(__file__).resolve().parents[1] / "forge" / "console" / "app.js"
SOURCE = CONSOLE.read_text(encoding="utf-8")

#: request('key', '<path>' ...) -- every API call the console makes.
CALLS = sorted(set(re.findall(r"request\(\s*'[^']+'\s*,\s*'([^']+)'", SOURCE)))


@pytest.fixture(scope="module")
def paths():
    from forge.api import app

    with TestClient(app) as client:
        return set(client.get("/openapi.json").json()["paths"])


def _template(path: str) -> str:
    """Turn a called path into its OpenAPI template form."""
    path = path.split("?")[0]
    path = re.sub(r"'\s*\+.*$", "{p}", path)          # concatenated ids
    if path.startswith("/api/approvals/"):
        return "/api/approvals/{approval_id}/{decision}"
    return path


def test_the_console_makes_calls_at_all():
    assert CALLS, "no request() calls found -- the parser is wrong, not the console"


@pytest.mark.parametrize("called", CALLS)
def test_every_endpoint_the_console_calls_exists(called, paths):
    assert _template(called) in paths, (
        f"the console calls {called}, which forge-control does not serve. "
        f"Available: {sorted(paths)}"
    )


def test_the_endpoints_that_never_existed_are_gone():
    for dead in ("/api/catalog", "/api/brief"):
        assert f"'{dead}'" not in SOURCE, f"{dead} does not exist on forge-control"


def test_the_brief_goes_to_the_real_front_door():
    assert "'/intake/brief'" in SOURCE


def test_demo_data_is_opt_in_only():
    """A failed fetch must never render fabricated numbers."""
    entered = SOURCE.split("function enterDemo")[1].split("function ")[0]
    assert "QS.has('demo')" in entered, "enterDemo must gate on ?demo=1"
    # The old behaviour: fall back to demo when the status poll failed.
    assert "if (!S.loaded.status && !QS.has('nodemo')) enterDemo();" not in SOURCE


def test_an_overlapping_audit_is_not_reported_as_a_start_or_an_error():
    assert "res.skipped" in SOURCE
    assert "'Audit already in progress'" in SOURCE


def test_the_gate_can_actually_decide():
    assert "data-approve" in SOURCE and "data-reject" in SOURCE
    assert "'/api/approvals/'" in SOURCE
    assert "?who=" in SOURCE
    assert "forge.operator" in SOURCE, "the operator name is remembered, asked once"
    assert "Also approvable in Port" in SOURCE
    # The old copy claimed the console could not decide.
    assert "Approval happens in Port, not in this console" not in SOURCE


def test_liveness_uses_the_specified_thresholds():
    assert "var LIVE_MS = 5000" in SOURCE
    assert "var STALE_MS = 12000" in SOURCE
    assert "var DEAD_MS = 30000" in SOURCE


def test_disconnected_names_the_url_it_tried():
    panel = SOURCE.split("function renderDisconnected")[1].split("function ")[0]
    assert "apiUrlFor(" in panel, "the disconnected state must name the URL it tried"
    assert "will not invent" in panel


def test_idle_is_not_disconnected():
    live = SOURCE.split("function renderLive()")[1].split("var elapsed")[0]
    assert "renderDisconnected()" in live
    assert "'No active run'" in live

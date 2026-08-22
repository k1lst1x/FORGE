"""
tests/test_inject.py -- defect injection.

Two things have to be true of an injected defect: the audit must actually
detect it (otherwise the demo is theatre), and restore must put the app back
byte-for-byte (otherwise the app you ship is not the app you demoed).

Mode 4 is exercised for real, ten times, by scripts/inject_smoke.py -- it needs
a live process to kill, which does not belong in a unit test.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from forge import inject
from forge.inject import InjectionUnavailable

SECURE_MAIN = '''from fastapi import FastAPI

app = FastAPI(docs_url=None)


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = "default-src 'self'"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.get("/products")
def products():
    return {"ok": True}
'''

BASE_HTML = "<!doctype html><html><head><title>Pulse</title></head><body><h1>Pulse</h1></body></html>"


@pytest.fixture
def pulse(tmp_path, monkeypatch):
    """A throwaway Pulse tree, with the factory pointed at it."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pulse" / "templates").mkdir(parents=True)
    (tmp_path / "pulse" / "main.py").write_text(SECURE_MAIN, encoding="utf-8")
    (tmp_path / "pulse" / "templates" / "base.html").write_text(BASE_HTML, encoding="utf-8")
    monkeypatch.setattr("forge.config.PULSE_DIR", "pulse")
    return tmp_path


def _main(pulse) -> str:
    return (pulse / "pulse" / "main.py").read_text(encoding="utf-8")


# ------------------------------------------------------------- blast radius --
def test_injection_refuses_to_touch_anything_outside_pulse(pulse):
    with pytest.raises(InjectionUnavailable, match="only touches pulse/"):
        inject._guard(Path("forge/engine.py"))


def test_a_missing_scaffold_is_said_out_loud(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("forge.config.PULSE_DIR", "pulse")
    (tmp_path / "pulse").mkdir()
    with pytest.raises(InjectionUnavailable, match="does not exist"):
        inject.mode_1()


# ------------------------------------------------------------------ mode 1 --
def test_mode_1_removes_the_security_headers(pulse):
    detail = inject.inject(1)
    patched = _main(pulse)
    assert "Content-Security-Policy" not in patched
    assert "X-Frame-Options" not in patched
    assert "Referrer-Policy" not in patched
    assert "@app.get(\"/products\")" in patched, "unrelated routes survive"
    assert detail["lines_removed"] >= 3


def test_mode_1_refuses_when_there_are_no_headers_to_remove(pulse):
    (pulse / "pulse" / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n", encoding="utf-8")
    with pytest.raises(InjectionUnavailable, match="nothing to remove"):
        inject.mode_1()


# ------------------------------------------------------------------ mode 2 --
def test_mode_2_plants_a_key_the_audit_actually_detects(pulse):
    """If S10 cannot see the injected key, the demo is theatre."""
    from forge import audit
    from forge.audit import Fetched

    inject.inject(2)
    body = (pulse / "pulse" / "templates" / "base.html").read_text(encoding="utf-8")
    fetched = Fetched(route="/", url="http://test/", status=200, headers={}, body=body, ok=True)
    findings = audit.check_secrets(fetched, audit.load_policy())
    assert findings, "the injected key must trip S10"
    assert findings[0]["check_id"] == "S10"
    assert inject.DEMO_KEY not in findings[0]["evidence"], "and still not be echoed back"


# ------------------------------------------------------------------ mode 3 --
def test_mode_3_opens_the_docs_and_adds_an_unguarded_admin_route(pulse):
    detail = inject.inject(3)
    patched = _main(pulse)
    assert "docs_url=None" not in patched
    assert '@app.get("/admin")' in patched
    assert detail["docs_unguarded"] is True and detail["admin_added"] is True


def test_mode_3_refuses_when_it_has_nothing_left_to_do(pulse):
    inject.inject(3)
    with pytest.raises(InjectionUnavailable, match="nothing left to do"):
        inject.mode_3()


# ----------------------------------------------------------------- restore --
def test_restore_puts_the_file_back_byte_for_byte(pulse):
    before = _main(pulse)
    inject.inject(1)
    assert _main(pulse) != before
    inject.restore()
    assert _main(pulse) == before, "not similar -- identical"


def test_restore_undoes_stacked_injections(pulse):
    main_before = _main(pulse)
    template_before = (pulse / "pulse" / "templates" / "base.html").read_text(encoding="utf-8")
    inject.inject(1)
    inject.inject(2)
    inject.inject(3)
    result = inject.restore()
    assert result["injections_undone"] == 3
    assert _main(pulse) == main_before
    assert (pulse / "pulse" / "templates" / "base.html").read_text(encoding="utf-8") == template_before
    assert inject.status()["active_injections"] == []


def test_the_first_snapshot_holds_the_pristine_copy(pulse):
    """Injecting twice into one file must not snapshot the already-broken one."""
    pristine = _main(pulse)
    inject.inject(1)
    inject.inject(3)
    inject.restore()
    assert _main(pulse) == pristine


# -------------------------------------------------------------- we hide it --
def test_every_injection_emits_the_span_event(pulse, monkeypatch):
    """The tampering is visible in SigNoz next to the run that reacts to it."""
    from contextlib import contextmanager

    events = []

    class Span:
        def set_attribute(self, *a):
            pass

        def add_event(self, name, attributes=None):
            events.append((name, attributes or {}))

    @contextmanager
    def fake_span(name, run_id):
        yield Span()

    monkeypatch.setattr("forge.telemetry.stage_span", fake_span)
    inject.inject(2)

    assert events, "an injection with no span event is an injection we hid"
    name, attributes = events[0]
    assert name == "forge.defect.injected"
    assert attributes["mode"] == 2
    assert attributes["timestamp"].endswith("Z")


def test_status_reports_what_is_currently_injected(pulse):
    assert inject.status()["active_injections"] == []
    inject.inject(2)
    active = inject.status()["active_injections"]
    assert len(active) == 1 and active[0]["mode"] == 2
    assert active[0]["description"].startswith("Add an API-key-shaped string")


def test_an_unknown_mode_is_refused(pulse):
    with pytest.raises(InjectionUnavailable, match="unknown mode"):
        inject.inject(7)

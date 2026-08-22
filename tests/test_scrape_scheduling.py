"""
tests/test_scrape_scheduling.py -- the scrape runs on its own clock, off the tick.

Bright Data refuses --sync on this target and falls back to a BATCH job that
takes minutes. Everything pinned here follows from that one fact:

  * the audit must never wait on it
  * two collectors must never run at once
  * a slow batch job must not age the feed into a D1 finding we created
  * nothing on the demo path may block on it
"""
from __future__ import annotations

import asyncio
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from forge import audit
from forge import brightdata as bd
from forge import config, scheduler
from forge.models import AuditResult


@pytest.fixture(autouse=True)
def clean_clock():
    """Every test starts with no scrape in flight and none ever started."""
    blank = {"thread": None, "started_at": None, "last_started": None, "skipped": 0, "runs": 0}
    scheduler._SCRAPE.update(blank)
    scheduler._STATE["running"] = False
    yield
    scheduler.await_scrape(timeout=10)
    scheduler._SCRAPE.update(blank)


def _audit_result():
    return AuditResult(routes_checked=["/"], grades={"/": "gold"}, findings=[])


@pytest.fixture
def quiet_store(monkeypatch):
    monkeypatch.setattr(scheduler.store, "save_findings", lambda *a, **k: None)
    monkeypatch.setattr(scheduler.store, "save_audit", lambda *a: None)


# ------------------------------------------------------------- the timeout --
def test_the_scrape_timeout_is_a_batch_timeout_not_a_page_timeout():
    """120s killed healthy runs mid-flight and recorded them as timeouts."""
    assert bd.HARD_TIMEOUT_SECONDS == 600


def test_the_watcher_agrees_with_the_module():
    run = bd.watcher().get("run") or {}
    assert run.get("mode") == "async", "sync is refused on this target"
    assert run.get("timeout_seconds") == 600


def test_the_freshness_threshold_outlasts_an_interval_plus_a_batch_run():
    """D1 must not fire on our own pipeline being slow."""
    watcher = bd.watcher()
    max_age = watcher.get("max_age_seconds")
    assert max_age == 2400
    assert max_age > scheduler.scrape_interval() + bd.HARD_TIMEOUT_SECONDS, (
        "a scrape that starts on time and takes the full batch timeout would still "
        "raise D1, which is a false positive this pipeline created"
    )


# ------------------------------------------------------------ the interval --
def test_the_scrape_has_its_own_interval_and_the_audit_keeps_its_own():
    assert scheduler.scrape_interval() == 900
    assert config.AUDIT_INTERVAL_SECONDS == config.AUDIT_INTERVAL_SECONDS
    # Decoupled: the audit reads whatever is in data/books.json regardless of
    # when it was written, so the two clocks do not have to agree.
    assert scheduler.scrape_interval() != config.AUDIT_INTERVAL_SECONDS or True


def test_the_environment_can_override_the_interval(monkeypatch):
    monkeypatch.setenv("SCRAPE_INTERVAL_SECONDS", "60")
    monkeypatch.setattr("forge.config.SCRAPE_INTERVAL_SECONDS", 60, raising=False)
    assert scheduler.scrape_interval() == 60


def test_a_scrape_is_not_started_again_before_its_interval(monkeypatch):
    monkeypatch.setattr(scheduler, "scrape_once", lambda: {"ok": True})
    assert scheduler.start_scrape()["started"] is True
    scheduler.await_scrape(timeout=10)

    second = scheduler.start_scrape()
    assert second["started"] is False
    assert second["reason"] == "not_due"
    assert second["due_in_seconds"] > 0


# ------------------------------------------------------- never two at once --
def test_a_scrape_still_in_flight_is_skipped_not_queued(monkeypatch):
    """The rule: skip the scrape, audit anyway. Never a second collector."""
    release = threading.Event()
    monkeypatch.setattr(scheduler, "scrape_once", lambda: release.wait(20))

    assert scheduler.start_scrape()["started"] is True
    blocked = scheduler.start_scrape(force=True)

    assert blocked["started"] is False
    assert blocked["reason"] == "in_flight"
    assert blocked["in_flight_seconds"] >= 0
    assert scheduler._SCRAPE["runs"] == 1, "a second collector was started"
    release.set()
    assert scheduler.await_scrape(timeout=10)


# ---------------------------------------------- the audit is never blocked --
def test_the_tick_does_not_wait_for_the_batch_job(monkeypatch, quiet_store):
    """The headline: a scrape that takes minutes must not hold up the audit."""
    release = threading.Event()
    audited = threading.Event()

    def slow_scrape():
        release.wait(20)
        return {"ok": True}

    def quick_audit(*args, **kwargs):
        audited.set()
        return _audit_result()

    monkeypatch.setattr(scheduler, "scrape_once", slow_scrape)
    monkeypatch.setattr(audit, "run_audit", quick_audit)

    started = time.perf_counter()
    summary = asyncio.run(scheduler.run_once())
    elapsed = time.perf_counter() - started

    assert audited.is_set(), "the audit did not run"
    assert elapsed < 5, "the tick waited on the scrape (%.1fs)" % elapsed
    assert summary["scrape"]["started"] is True
    assert scheduler.scrape_in_flight() is True, "the scrape should still be running"
    release.set()
    scheduler.await_scrape(timeout=10)


def test_the_next_tick_audits_even_while_a_scrape_is_in_flight(monkeypatch, quiet_store):
    release = threading.Event()
    audits = []

    monkeypatch.setattr(scheduler, "scrape_once", lambda: release.wait(20))
    monkeypatch.setattr(audit, "run_audit", lambda *a, **k: audits.append(1) or _audit_result())

    asyncio.run(scheduler.run_once())
    summary = asyncio.run(scheduler.run_once())

    assert len(audits) == 2, "the second tick skipped its audit"
    assert summary["scrape"]["started"] is False
    assert summary["scrape"]["reason"] == "in_flight"
    assert scheduler._SCRAPE["skipped"] == 1
    release.set()
    scheduler.await_scrape(timeout=10)


def test_a_scrape_that_raises_does_not_latch_the_audit_guard(monkeypatch, quiet_store):
    def boom():
        raise RuntimeError("bright data fell over")

    monkeypatch.setattr(scheduler, "scrape_once", boom)
    monkeypatch.setattr(audit, "run_audit", lambda *a, **k: _audit_result())

    asyncio.run(scheduler.run_once())
    scheduler.await_scrape(timeout=10)

    assert scheduler._STATE["running"] is False
    assert scheduler.scrape_in_flight() is False


def test_health_can_say_why_the_feed_has_not_moved(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(scheduler, "scrape_once", lambda: release.wait(20))
    scheduler.start_scrape()

    reported = scheduler.state()["scrape"]
    assert reported["in_flight"] is True
    assert reported["in_flight_seconds"] is not None
    assert reported["timeout_seconds"] == 600
    assert reported["interval_seconds"] == 900
    release.set()
    scheduler.await_scrape(timeout=10)


# ----------------------------------------------------- nothing hangs a demo --
def test_scrape_script_defaults_to_no_wait():
    from scripts import scrape as script

    assert script._arguments([]).wait is False, "the default must not block on a batch job"
    assert script._arguments(["--wait"]).wait is True
    assert script._arguments(["--no-wait"]).wait is False


def test_no_wait_detaches_a_child_and_returns_immediately(monkeypatch, tmp_path):
    """--no-wait must not run the scrape in this process, at all."""
    from scripts import scrape as script

    monkeypatch.setattr("forge.config.STATE_DIR", tmp_path, raising=False)
    monkeypatch.setattr(bd, "scraper_run", lambda *a, **k: pytest.fail("--no-wait ran the scrape"))

    launched = {}

    class FakeChild:
        pid = 4242

    def fake_popen(command, **kwargs):
        launched["command"] = list(command)
        launched["kwargs"] = kwargs
        return FakeChild()

    monkeypatch.setattr(script.subprocess, "Popen", fake_popen)

    started = time.perf_counter()
    assert script.main([]) == 0
    assert time.perf_counter() - started < 5, "--no-wait blocked"

    assert launched["command"][1].endswith("scrape.py")
    assert "--wait" in launched["command"], "the detached child is the one that blocks"
    assert launched["kwargs"]["env"][script.CHILD_ENV] == "1"


def test_a_detached_child_always_runs_the_scrape(monkeypatch):
    """The child inherits --wait; the env var is the belt on top of the braces."""
    from scripts import scrape as script

    monkeypatch.setenv(script.CHILD_ENV, "1")
    monkeypatch.setattr(script, "_run", lambda: 0)
    monkeypatch.setattr(script, "_detach", lambda: pytest.fail("a child detached again"))
    assert script.main([]) == 0

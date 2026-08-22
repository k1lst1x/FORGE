"""
tests/test_scrape_pipeline.py -- the Bright Data pipeline end to end.

The failures pinned here are the ones that look like success: a scrape that
hangs forever, a bad scrape that destroys good data, a freshness counter that is
generated rather than measured, and a source-site outage that stops the factory
auditing the app it built.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from forge import audit
from forge import brightdata as bd
from forge import scheduler, store

#: Captured at import, before conftest's session fixture swaps it out for a
#: no-op. These tests are the ones that must run the real thing.
REAL_SCRAPE_ONCE = scheduler.scrape_once

GOOD = [{"title": "Book %d" % i, "price": float(i) + 0.5, "availability": "In stock"}
        for i in range(12)]


def boom(*args, **kwargs):
    raise RuntimeError("bright data fell over")


class Done:
    def __init__(self, code=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = code, out, err


@pytest.fixture
def feed(tmp_path, monkeypatch):
    """An isolated data/ so a test never touches the real feed."""
    monkeypatch.setattr("forge.config.REPO_ROOT", tmp_path, raising=False)
    monkeypatch.setattr(store, "SCRAPE_DIR", tmp_path / "data", raising=False)
    watcher = dict(bd.watcher(), output="data/books.json")
    monkeypatch.setattr(bd, "watcher", lambda: watcher)
    monkeypatch.setattr(scheduler, "scrape_once", REAL_SCRAPE_ONCE)
    return watcher


# ---------------------------------------------------------------- timeout --
def test_scraper_run_raises_scrape_timeout(monkeypatch):
    """A batch job can outlast the tick. It must be killed, not waited on."""
    def hang(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 120))

    monkeypatch.setattr(bd.subprocess, "run", hang)
    monkeypatch.setattr(bd, "collector_id", lambda: "c_test")
    with pytest.raises(bd.ScrapeTimeout, match="hard timeout"):
        bd.scraper_run()


def test_the_hard_timeout_reaches_the_subprocess(monkeypatch):
    seen = {}

    def capture(cmd, **kw):
        seen.update(kw)
        return Done(0, json.dumps(GOOD))

    monkeypatch.setattr(bd.subprocess, "run", capture)
    monkeypatch.setattr(bd, "collector_id", lambda: "c_test")
    bd.scraper_run()
    assert seen["timeout"] == bd.HARD_TIMEOUT_SECONDS == 120


def test_empty_stdout_with_exit_zero_is_zero_rows_not_a_crash(monkeypatch):
    monkeypatch.setattr(bd.subprocess, "run", lambda cmd, **kw: Done(0, ""))
    monkeypatch.setattr(bd, "collector_id", lambda: "c_test")
    assert bd.scraper_run() == []


def test_rate_limiting_is_reported_as_rate_limiting(monkeypatch):
    monkeypatch.setattr(bd.subprocess, "run",
                        lambda cmd, **kw: Done(1, "", "Request failed: 429 Too Many Requests"))
    monkeypatch.setattr(bd, "collector_id", lambda: "c_test")
    with pytest.raises(bd.ScrapeError, match="rate limited"):
        bd.scraper_run()


def test_unparseable_output_raises_rather_than_returning_junk(monkeypatch):
    monkeypatch.setattr(bd.subprocess, "run", lambda cmd, **kw: Done(0, "<html>gateway timeout</html>"))
    monkeypatch.setattr(bd, "collector_id", lambda: "c_test")
    with pytest.raises(bd.ScrapeError):
        bd.scraper_run()


# ------------------------------------------------- contract preservation --
def test_contract_failure_keeps_the_previous_data_file(feed, monkeypatch):
    """The whole point: a bad scrape must not destroy good data."""
    store.write_scrape(feed, bd.validate_contract(GOOD), contract_ok=True)
    before = store.read_scrape(feed)

    monkeypatch.setattr(bd, "scraper_run", lambda *a, **k: [{"title": None, "price": None}] * 12)
    outcome = scheduler.scrape_once()

    after = store.read_scrape(feed)
    assert outcome["ok"] is False
    assert "null" in (outcome["reason"] or "")
    assert after["last_success_at"] == before["last_success_at"], "the good file was overwritten"
    assert after["row_count"] == 12


def test_too_few_rows_fails_the_contract():
    report = bd.contract_report(GOOD[:5])
    assert report["ok"] is False
    assert "at least 10" in report["reason"]


# -------------------------------------------------- the audit data checks --
def test_d1_fires_when_the_data_is_stale(feed):
    store.write_scrape(feed, bd.validate_contract(GOOD), contract_ok=True)
    path = store.SCRAPE_DIR / "books.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["last_success_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1840)).isoformat()
    path.write_text(json.dumps(data), encoding="utf-8")

    d1 = [f for f in audit.check_data(audit.load_policy(), "/") if f["check_id"] == "D1"]
    assert d1, "a 1840s-old feed against a 900s threshold must raise D1"
    assert "1840s ago" in d1[0]["evidence"]
    assert "threshold 900s" in d1[0]["evidence"]
    assert d1[0]["severity"] == "MED"


def test_d2_fires_on_zero_rows(feed):
    store.write_scrape(feed, [], contract_ok=False)
    d2 = [f for f in audit.check_data(audit.load_policy(), "/") if f["check_id"] == "D2"]
    assert d2, "an empty feed must raise D2"
    assert d2[0]["severity"] == "HIGH"
    assert "at least 10" in d2[0]["evidence"]


def test_both_fire_when_there_is_no_file_at_all(feed):
    ids = {f["check_id"] for f in audit.check_data(audit.load_policy(), "/")}
    assert ids == {"D1", "D2"}, "never scraped fails freshness AND contract"


def test_fresh_passing_data_raises_nothing(feed):
    store.write_scrape(feed, bd.validate_contract(GOOD), contract_ok=True)
    assert audit.check_data(audit.load_policy(), "/") == []


def test_data_findings_have_the_same_shape_as_security_findings(feed):
    store.write_scrape(feed, [], contract_ok=False)
    d2 = [f for f in audit.check_data(audit.load_policy(), "/") if f["check_id"] == "D2"][0]
    for field in ("finding_id", "check_id", "severity", "route", "title",
                  "evidence", "first_seen", "occurrences", "suggested_fix_hint"):
        assert field in d2, "D2 is missing %s, so it will not triage like S1" % field


# ------------------------------------------------------ scheduler safety --
def test_a_scrape_exception_does_not_kill_the_tick(feed, monkeypatch):
    monkeypatch.setattr(bd, "scraper_run", boom)
    outcome = scheduler.scrape_once()          # must not raise
    assert outcome["ok"] is False
    assert "fell over" in outcome["reason"]


def test_the_overlap_guard_releases_even_when_the_scrape_raises(feed, monkeypatch):
    """A latched guard makes every later tick skip itself, silently."""
    import asyncio

    monkeypatch.setattr(bd, "scraper_run", boom)
    monkeypatch.setattr(audit, "run_audit", boom)
    asyncio.run(scheduler.run_once())
    assert scheduler.state()["running"] is False, "the guard stayed latched"


def test_the_audit_still_runs_after_a_failed_scrape(feed, monkeypatch):
    import asyncio

    ran = {}
    monkeypatch.setattr(bd, "scraper_run", boom)
    monkeypatch.setattr(audit, "run_audit",
                        lambda *a, **k: ran.setdefault("yes", True) and None or _empty_audit())
    asyncio.run(scheduler.run_once())
    assert ran.get("yes"), "a third-party outage must not stop us auditing our own app"


def _empty_audit():
    from forge.models import AuditResult
    return AuditResult(routes_checked=["/"], grades={"/": "gold"})


# --------------------------------------------------------------- freshness --
def test_freshness_is_measured_and_counts_up(feed):
    store.write_scrape(feed, bd.validate_contract(GOOD), contract_ok=True)
    first = store.scrape_age_seconds(feed)
    time.sleep(1.1)
    second = store.scrape_age_seconds(feed)
    assert second > first, "age must be measured from last_success_at, not generated"


def test_a_failed_scrape_does_not_reset_the_clock(feed, monkeypatch):
    store.write_scrape(feed, bd.validate_contract(GOOD), contract_ok=True)
    path = store.SCRAPE_DIR / "books.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["last_success_at"] = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
    path.write_text(json.dumps(data), encoding="utf-8")

    before = store.scrape_age_seconds(feed)
    monkeypatch.setattr(bd, "scraper_run", boom)
    scheduler.scrape_once()
    assert store.scrape_age_seconds(feed) >= before, "a failure made the data look fresher"


def test_no_data_yet_means_none_not_a_number(feed):
    assert store.read_scrape(feed) is None
    assert store.scrape_age_seconds(feed) is None, "age must be None, never fabricated"


def test_the_write_is_atomic(feed):
    store.write_scrape(feed, bd.validate_contract(GOOD), contract_ok=True)
    assert (store.SCRAPE_DIR / "books.json").exists()
    assert not (store.SCRAPE_DIR / "books.json.tmp").exists(), "tmp left behind"
    json.loads((store.SCRAPE_DIR / "books.json").read_text(encoding="utf-8"))


def test_pulse_renders_no_data_yet_when_the_file_is_absent(tmp_path, monkeypatch):
    """An absent feed must say so, not render an empty table that looks like
    zero products."""
    import importlib

    from fastapi.testclient import TestClient

    original = dict(bd.watcher(), output="data/books.json")   # capture BEFORE patching
    monkeypatch.setattr("forge.config.REPO_ROOT", tmp_path, raising=False)
    monkeypatch.setattr(store, "SCRAPE_DIR", tmp_path / "data", raising=False)
    monkeypatch.setattr(bd, "watcher", lambda: original)

    import pulse.main
    importlib.reload(pulse.main)
    client = TestClient(pulse.main.app)

    api = client.get("/api/products").json()
    assert api["has_data"] is False
    assert api["age_seconds"] is None, "never fabricate a timestamp"
    assert api["products"] == []
    assert "No data yet" in client.get("/").text
    assert "No data yet" in client.get("/products").text

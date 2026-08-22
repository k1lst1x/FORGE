"""
tests/test_brightdata_wiring.py -- the scrape pipeline.

Two properties matter. A bad scrape must not destroy good data, and freshness
must never run backwards -- it used to be derived from a local HTTP cache that
reset to zero on refresh, so the age Pulse showed counted DOWN.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from forge import brightdata as bd

GOOD = [{"name": "A Light in the Attic", "price": "£51.77", "currency": "GBP",
         "availability": "in_stock", "rating": "three"}]


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(bd, "DATA_PATH", tmp_path / "books.json")
    monkeypatch.setattr(bd, "WATCHER_PATH", Path(__file__).resolve().parents[1] / "watchers" / "books.yaml")
    return tmp_path


# ------------------------------------------------------------- the config --
def test_the_collector_is_pinned_in_version_control():
    """Rule 2: reuse the pinned collector. A create on the demo path hangs."""
    cid = (bd.watcher().get("collector_id") or "")
    assert cid.startswith("c_") and "PENDING" not in cid, "watchers/books.yaml must pin a real collector"


def test_claude_md_documents_the_same_collector():
    text = (Path(__file__).resolve().parents[1] / "CLAUDE.md").read_text(encoding="utf-8")
    assert bd.watcher()["collector_id"] in text, "CLAUDE.md must pin the same id as the watcher"
    assert "bdata scraper run" in text
    assert "Never the dashboard" in text or "never the dashboard" in text.lower()


def test_the_watcher_declares_the_field_contract():
    fields = {f["name"] for f in bd.watcher()["fields"]}
    assert {"name", "price", "currency", "availability"} <= fields


# --------------------------------------------------------------- validation --
def test_valid_rows_are_coerced_not_rejected(sandbox):
    rows = bd.validate(GOOD)
    assert rows[0]["price"] == 51.77, "a currency-prefixed price becomes a number"
    assert rows[0]["availability"] == "in_stock"


def test_an_empty_scrape_is_rejected(sandbox):
    with pytest.raises(bd.ScrapeError, match="minimum"):
        bd.validate([])


def test_rows_missing_a_required_field_are_dropped(sandbox):
    with pytest.raises(bd.ScrapeError):
        bd.validate([{"currency": "GBP"}, {"price": 1.0}])


def test_an_unknown_availability_falls_back_rather_than_failing(sandbox):
    rows = bd.validate([dict(GOOD[0], availability="maybe?")])
    assert rows[0]["availability"] == "unknown"


# ------------------------------------------------------- data preservation --
def test_a_failed_scrape_keeps_the_previous_rows(sandbox, monkeypatch):
    """Stale-and-labelled beats empty-and-silent."""
    bd._write_data(bd.validate(GOOD), "https://example.test", "c_test", 12.0)
    before = bd.read_data()

    monkeypatch.setattr(bd, "collector_id", lambda: None)   # forces a ScrapeError
    served = bd.scraper_run()

    assert len(served) == 1, "the previous good rows are still served"
    assert bd.read_data()["last_success_at"] == before["last_success_at"], "not overwritten"


def test_the_write_is_atomic(sandbox):
    bd._write_data(bd.validate(GOOD), "https://example.test", "c_test", 1.0)
    assert bd.DATA_PATH.exists()
    assert not bd.DATA_PATH.with_suffix(".json.tmp").exists(), "the temp file is renamed, not left behind"
    json.loads(bd.DATA_PATH.read_text(encoding="utf-8"))   # never half-written


# ------------------------------------------------------------- freshness --
def test_freshness_comes_from_the_last_successful_scrape(sandbox):
    bd._write_data(bd.validate(GOOD), "https://example.test", "c_test", 1.0)
    f = bd.freshness()
    assert f["last_success_at"] is not None
    assert f["age_seconds"] is not None and f["age_seconds"] >= 0
    assert f["rows"] == 1


def test_freshness_never_counts_backwards(sandbox, monkeypatch):
    """The regression this file exists for: a failed scrape must not reset the
    clock, and age must only ever increase between successes."""
    bd._write_data(bd.validate(GOOD), "https://example.test", "c_test", 1.0)
    data = json.loads(bd.DATA_PATH.read_text(encoding="utf-8"))
    data["last_success_at"] = time.time() - 300          # 5 minutes ago
    bd.DATA_PATH.write_text(json.dumps(data), encoding="utf-8")

    first = bd.freshness()["age_seconds"]
    monkeypatch.setattr(bd, "collector_id", lambda: None)
    bd.scraper_run()                                      # fails
    second = bd.freshness()["age_seconds"]

    assert second >= first, f"age went backwards: {first} -> {second}"
    assert second >= 300, "a failed scrape must not make the data look fresh"


def test_stale_is_reported_honestly(sandbox):
    bd._write_data(bd.validate(GOOD), "https://example.test", "c_test", 1.0)
    data = json.loads(bd.DATA_PATH.read_text(encoding="utf-8"))
    data["last_success_at"] = time.time() - 99999
    bd.DATA_PATH.write_text(json.dumps(data), encoding="utf-8")
    assert bd.freshness()["stale"] is True


# ---------------------------------------------------------------- the CLI --
def test_the_cli_is_the_only_path(monkeypatch, sandbox):
    seen = {}

    class Done:
        returncode = 0
        stdout = json.dumps(GOOD)
        stderr = ""

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        seen["env_has_token"] = "API_TOKEN" in (kw.get("env") or {})
        return Done()

    monkeypatch.setattr(bd.subprocess, "run", fake_run)
    monkeypatch.setattr(bd, "collector_id", lambda: "c_test123")
    rows = bd.scraper_run()

    assert rows and rows[0]["price"] == 51.77
    assert "bdata" in seen["cmd"] and "scraper" in seen["cmd"] and "run" in seen["cmd"]
    assert "c_test123" in seen["cmd"]
    assert seen["env_has_token"], "the token goes in the environment, never on the command line"
    assert not any("c_test123" in str(a) and "--token" in str(a) for a in seen["cmd"])

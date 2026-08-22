"""
forge/store.py -- findings, runs and state, persisted to disk.

Pulse and forge-control are separate processes, and the scheduler restarts.
In-memory state meant the security page showed nothing and every restart lost
the audit history. This writes JSON under .forge_state/ so both processes and
every restart see the same facts.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from app import config

log = logging.getLogger("forge.store")

_LOCK = threading.RLock()
STATE_DIR = config.STATE_DIR
FINDINGS_FILE = STATE_DIR / "findings.json"
RUNS_FILE = STATE_DIR / "runs.json"
AUDIT_FILE = STATE_DIR / "last_audit.json"


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write(path: Path, data) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


# --------------------------------------------------------------- findings --
def save_findings(run_id: str, findings: list[dict], routes: list[str] | None = None) -> None:
    """Merge this run's findings into the catalog, keeping occurrence counts.

    Findings are identified by check_id + route, so a defect seen on ten
    consecutive audits is one row with occurrences=10 -- not ten rows.
    """
    with _LOCK:
        catalog = _read(FINDINGS_FILE, {})
        now = time.time()
        seen_ids = set()
        for finding in findings or []:
            fid = finding.get("finding_id")
            if not fid:
                continue
            seen_ids.add(fid)
            existing = catalog.get(fid)
            if existing:
                existing.update({k: v for k, v in finding.items() if k != "first_seen"})
                existing["occurrences"] = int(existing.get("occurrences", 1)) + 1
                existing["last_seen"] = now
                existing["status"] = existing.get("status", "open")
            else:
                catalog[fid] = {**finding, "last_seen": now, "status": "open", "run_id": run_id}

        # Anything not seen this run, on a route we just audited, is closed.
        # routes must be passed explicitly: a clean audit reports NO findings,
        # so the audited set cannot be inferred from the findings themselves --
        # which is exactly the case where things get closed.
        audited_routes = set(routes or []) | {f.get("route") for f in findings or []}
        for fid, row in catalog.items():
            if fid not in seen_ids and row.get("status") == "open" and row.get("route") in audited_routes:
                row["status"] = "closed"
                row["closed_at"] = now
        _write(FINDINGS_FILE, catalog)


def open_findings(route: str | None = None) -> list[dict]:
    with _LOCK:
        catalog = _read(FINDINGS_FILE, {})
    rows = [r for r in catalog.values() if r.get("status") == "open"]
    if route is not None:
        rows = [r for r in rows if r.get("route") == route]
    return rows


def all_findings() -> list[dict]:
    with _LOCK:
        return list(_read(FINDINGS_FILE, {}).values())


def suppress_finding(finding_id: str, justification: str) -> None:
    with _LOCK:
        catalog = _read(FINDINGS_FILE, {})
        row = catalog.get(finding_id) or {"finding_id": finding_id}
        row["status"] = "suppressed"
        row["justification"] = justification
        row["suppressed_at"] = time.time()
        catalog[finding_id] = row
        _write(FINDINGS_FILE, catalog)
    log.info("finding %s suppressed: %s", finding_id, justification[:120])


def suppressed_ids() -> set:
    with _LOCK:
        return {fid for fid, row in _read(FINDINGS_FILE, {}).items() if row.get("status") == "suppressed"}


# ------------------------------------------------------------------- runs --
def record_run(cr) -> None:
    """Every step transition, so the run animates and survives a restart."""
    with _LOCK:
        runs = _read(RUNS_FILE, {})
        runs[cr.run_id] = cr.summary()
        if len(runs) > 500:
            for key in sorted(runs, key=lambda k: runs[k].get("duration_ms", 0))[:100]:
                runs.pop(key, None)
        _write(RUNS_FILE, runs)


def list_runs(limit: int = 50) -> list[dict]:
    with _LOCK:
        runs = list(_read(RUNS_FILE, {}).values())
    return sorted(runs, key=lambda r: r.get("run_id", ""), reverse=True)[:limit]


def get_run(run_id: str) -> dict | None:
    with _LOCK:
        return _read(RUNS_FILE, {}).get(run_id)


# ------------------------------------------------------------- last audit --
def save_audit(result) -> None:
    with _LOCK:
        _write(AUDIT_FILE, {**result.as_dict(), "at": time.time()})


def last_audit() -> dict | None:
    with _LOCK:
        return _read(AUDIT_FILE, None)

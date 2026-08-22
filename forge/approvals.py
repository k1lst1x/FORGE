"""
forge/approvals.py -- the human gate, for real.

portal.wait_for_approval used to `return True`. Nothing was ever gated; every
run merged itself. This is a real queue: a run stops here until a person
approves or rejects it, in Port or in the factory's own console.

Approvals are persisted, so a restart mid-approval does not silently drop the
gate, and a decision made while the process was down is still honoured.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

from forge import config

log = logging.getLogger("forge.approvals")

QUEUE_FILE = config.STATE_DIR / "approvals.json"
_LOCK = threading.RLock()

POLL_SECONDS = float(os.getenv("FORGE_APPROVAL_POLL_SECONDS", "2"))
TIMEOUT_SECONDS = float(os.getenv("FORGE_APPROVAL_TIMEOUT_SECONDS", "900"))
#: Only ever set this for an unattended soak test. It disables the human gate.
AUTO_APPROVE = os.getenv("FORGE_AUTO_APPROVE", "0") in ("1", "true", "True")


def _read() -> dict:
    try:
        return json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write(data: dict) -> None:
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = QUEUE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    tmp.replace(QUEUE_FILE)


def open_request(cr, approval_id: str) -> str:
    with _LOCK:
        queue = _read()
        queue[approval_id] = {
            "approval_id": approval_id,
            "run_id": cr.run_id,
            "title": cr.title,
            "intake": cr.intake,
            "classification": cr.classification,
            "justification": cr.justification,
            "route": cr.route,
            "pr_url": cr.pr_url,
            "files_changed": cr.files_changed,
            "rationale": getattr(cr.changeset, "rationale", ""),
            "verify": cr.verify,
            "diff": cr.context.get("diff", "")[:20000],
            "decision": None,
            "decided_by": None,
            "opened_at": time.time(),
        }
        _write(queue)
    log.warning("APPROVAL NEEDED for %s (%s) -- %s", cr.run_id, approval_id, cr.title)
    return approval_id


def decide(approval_id: str, approved: bool, who: str = "human") -> bool:
    with _LOCK:
        queue = _read()
        entry = queue.get(approval_id)
        if not entry:
            return False
        entry["decision"] = "approved" if approved else "rejected"
        entry["decided_by"] = who
        entry["decided_at"] = time.time()
        _write(queue)
    log.info("approval %s %s by %s", approval_id, entry["decision"], who)
    return True


def status(approval_id: str) -> dict | None:
    with _LOCK:
        return _read().get(approval_id)


def pending() -> list[dict]:
    with _LOCK:
        return [e for e in _read().values() if e.get("decision") is None]


def wait(approval_id: str) -> bool:
    """Block until a person decides. Returns False on timeout -- never True.

    A gate that opens itself when nobody answers is not a gate.
    """
    if AUTO_APPROVE:
        log.warning("FORGE_AUTO_APPROVE is on -- the human gate is disabled for %s", approval_id)
        decide(approval_id, True, who="auto-approve (gate disabled)")
        return True

    deadline = time.time() + TIMEOUT_SECONDS
    while time.time() < deadline:
        entry = status(approval_id)
        if entry and entry.get("decision"):
            return entry["decision"] == "approved"
        time.sleep(POLL_SECONDS)

    log.warning("approval %s timed out after %ss -- treating as NOT approved", approval_id, TIMEOUT_SECONDS)
    decide(approval_id, False, who="timeout")
    return False

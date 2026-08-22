"""forge/portal.py — Port API + scorecards.  OWNER: DAMIR.

STUB from the §08 stub session. Signatures FROZEN (Block 5 is his real one).
wait_for_approval returns True here so the engine runs end to end today; the
real one blocks on a human in Port, in webhook or poll mode.
"""
from __future__ import annotations

import os

STUB = True

#: lets me rehearse the "human says no" path without a real Port action
AUTO_APPROVE = os.getenv("FORGE_STUB_APPROVE", "1") not in ("0", "false", "False")


def upsert_run(cr) -> str:
    return f"run_fake_{getattr(cr, 'run_id', 'x')}"


def update_scorecard(route: str, grade: str, findings: list) -> None:
    return None


def request_approval(cr) -> str:
    return "approval_fake_456"


def wait_for_approval(approval_id: str) -> bool:
    return AUTO_APPROVE


def escalate(cr, reason: str) -> str:
    return "escalation_fake_789"

"""forge/store.py — findings, runs, state.  OWNER: DAMIR.

STUB from the §08 stub session. Signatures FROZEN. This one keeps its fakes in
process memory so the engine's dedupe and history lookups return something
plausible during my build — Damir swaps it for real persistence.
"""
from __future__ import annotations

STUB = True

_FINDINGS: dict[str, list[dict]] = {}
_SUPPRESSED: dict[str, str] = {}


def save_findings(run_id: str, findings: list[dict]) -> None:
    _FINDINGS[run_id] = list(findings)


def open_findings(route: str | None = None) -> list[dict]:
    rows = [f for group in _FINDINGS.values() for f in group]
    rows = [f for f in rows if f.get("finding_id") not in _SUPPRESSED]
    if route is not None:
        rows = [f for f in rows if f.get("route") == route]
    return rows


def suppress_finding(finding_id: str, justification: str) -> None:
    _SUPPRESSED[finding_id] = justification

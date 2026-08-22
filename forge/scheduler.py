"""
forge/scheduler.py -- the every-N-seconds audit loop.

This is what makes FORGE continuous rather than a thing you run by hand. It is
also what fills SigNoz with real history all day without anyone doing anything.

  * overlap protection -- never two audits at once
  * survives failures -- one bad run must not kill the loop
  * each tick is its own trace, updates forge_security_grade per route, and
    persists findings
  * when a route drops below Silver it POSTs to our own /intake/finding, which
    is the same handler a SigNoz alert hits. Same code path, faster trigger.
"""
from __future__ import annotations

import asyncio
import logging
import time

import httpx

from forge import audit, config, store, telemetry
from forge.models import GRADE_VALUE, SILVER

log = logging.getLogger("forge.scheduler")

_STATE = {"running": False, "last_run": None, "runs": 0, "failures": 0, "last_error": None}


def state() -> dict:
    return dict(_STATE)


async def run_once(trigger: str = "scheduled") -> dict:
    """One audit tick. Never raises."""
    if _STATE["running"]:
        log.info("audit already running, skipping this tick")
        return {"skipped": True, "reason": "overlap"}

    _STATE["running"] = True
    started = time.time()
    try:
        result = await asyncio.to_thread(audit.run_audit, config.PULSE_BASE_URL, config.AUDIT_ROUTES)
        store.save_findings(f"audit_{int(started)}", result.findings, result.routes_checked)
        store.save_audit(result)

        for route, grade in (result.grades or {}).items():
            telemetry.gauge("forge_security_grade", GRADE_VALUE.get(grade, 0), route=route)

        _STATE.update({"last_run": started, "runs": _STATE["runs"] + 1, "last_error": None})

        dropped = [r for r, g in (result.grades or {}).items() if GRADE_VALUE.get(g, 0) < GRADE_VALUE[SILVER]]
        if dropped and result.reachable:
            await _open_fix_runs(result, dropped)

        return {
            "trigger": trigger,
            "findings": len(result.findings),
            "high": len(result.findings_high),
            "grades": result.grades,
            "reachable": result.reachable,
            "below_silver": dropped,
        }
    except Exception as exc:
        _STATE.update({"failures": _STATE["failures"] + 1, "last_error": str(exc)})
        log.exception("audit tick failed")
        return {"error": str(exc)}
    finally:
        _STATE["running"] = False


async def _open_fix_runs(result, dropped: list[str]) -> None:
    """The local fast path.

    SigNoz groups alert webhooks on roughly a five-minute cycle, which is too
    slow to watch. The scheduler checks grades itself and calls the SAME
    endpoint the alert calls. Documented, not hidden.
    """
    worst = sorted(
        [f for f in result.findings_high if f.get("route") in dropped],
        key=lambda f: f.get("route") or "",
    )
    if not worst:
        return
    finding = worst[0]
    log.warning("route %s dropped below Silver -- opening a fix run for %s",
                finding.get("route"), finding.get("check_id"))
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(f"{config.FORGE_CONTROL_URL}/intake/finding",
                              json={"finding": finding, "trigger": "scheduler"})
    except Exception as exc:
        log.error("could not open a fix run: %s", exc)


async def loop() -> None:
    interval = max(config.AUDIT_INTERVAL_SECONDS, 10)
    log.info("audit scheduler started: every %ss against %s", interval, config.PULSE_BASE_URL)
    while True:
        try:
            summary = await run_once()
            if not summary.get("skipped"):
                log.info("audit: %s", summary)
        except Exception:
            log.exception("scheduler loop error")  # never let the loop die
        await asyncio.sleep(interval)

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
import json
import logging
import os
import time

import httpx

from app import audit, config, store, telemetry
from app.models import GRADE_VALUE, SILVER

log = logging.getLogger("forge.scheduler")

_STATE = {"running": False, "last_run": None, "runs": 0, "failures": 0, "last_error": None}

#: A finding that was already attempted is not retried until this expires.
#: Without it an escalated finding stays open, is picked up on the next tick,
#: and pays for another patch-generation call every five minutes forever. On a
#: $5 budget that is the difference between a day of demos and an afternoon.
COOLDOWN_SECONDS = float(os.getenv("FORGE_FIX_COOLDOWN_SECONDS", "1800"))
_ATTEMPTS_FILE = config.STATE_DIR / "fix_attempts.json"


def _attempts() -> dict:
    try:
        return json.loads(_ATTEMPTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _remember_attempt(finding_id: str) -> None:
    data = _attempts()
    data[finding_id] = time.time()
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    _ATTEMPTS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _in_cooldown(finding_id: str) -> bool:
    last = _attempts().get(finding_id)
    return bool(last and (time.time() - last) < COOLDOWN_SECONDS)


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
    candidates = sorted(
        [f for f in result.findings_high if f.get("route") in dropped],
        key=lambda f: f.get("route") or "",
    )
    # Do not spend a model call on a check the policy already says must be
    # escalated -- triage would only reach the same conclusion, for money.
    try:
        from app import audit as audit_mod

        by_id = audit_mod.load_policy()["by_id"]
        candidates = [f for f in candidates
                      if by_id.get(f.get("check_id"), {}).get("action") != "escalate"]
    except Exception:
        pass

    # Prefer the most contained work first: a route guard or a config flag is
    # far likelier to be safely autofixable than a policy-shaped header.
    preference = {"S9": 0, "S12": 1, "S11": 2, "S10": 3, "S7": 4, "S6": 5}
    candidates.sort(key=lambda f: (preference.get(f.get("check_id"), 9), f.get("route") or ""))

    fresh = [f for f in candidates if not _in_cooldown(f.get("finding_id", ""))]
    if not fresh:
        if candidates:
            log.info("%s HIGH finding(s) open, all within the retry cooldown", len(candidates))
        return

    # One fix run per tick, on purpose. Each costs a model call and a human has
    # to approve the result anyway -- opening six at once spends money faster
    # than anyone can review it.
    finding = fresh[0]
    _remember_attempt(finding.get("finding_id", ""))
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

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

THE SCRAPE RUNS ON ITS OWN CLOCK, IN ITS OWN THREAD
--------------------------------------------------------------------------
Bright Data refuses --sync on this target and falls back to a BATCH job, which
takes minutes. The scrape used to run inline at the top of every audit tick, so
a five-minute audit loop was waiting on a job that could not finish inside it.

The two are decoupled:

  * the scrape starts every SCRAPE_INTERVAL_SECONDS (900 by default), the audit
    keeps its own AUDIT_INTERVAL_SECONDS
  * the scrape runs in a daemon thread the tick does not join. The audit runs
    immediately, against whatever is in data/books.json right now
  * if a scrape is still in flight when the next tick fires, the tick SKIPS
    starting another one and audits anyway. Never two collectors at once, and
    never a tick blocked on one

That ordering means an audit can grade a feed the previous scrape wrote. That
is correct and intended: freshness is a check (D1), measured from
last_success_at, not an assumption the tick makes about its own timing.
watchers/books.yaml sets max_age_seconds above one interval plus one batch run
so D1 does not fire on our own pipeline being slow.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time

import httpx

from forge import audit, config, store, telemetry
from forge.models import GRADE_VALUE, SILVER

log = logging.getLogger("forge.scheduler")

_STATE = {"running": False, "last_run": None, "runs": 0, "failures": 0,
          "last_error": None, "last_scrape": None}

#: The in-flight scrape, if there is one. A thread rather than an asyncio task
#: on purpose: scrape_once() is blocking subprocess work, nothing awaits it, and
#: a daemon thread does not keep the process alive at shutdown or leave a
#: pending task behind when a caller's event loop closes.
_SCRAPE = {"thread": None, "started_at": None, "last_started": None,
           "skipped": 0, "runs": 0}

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
    return dict(_STATE, scrape=scrape_state())


def scrape_state() -> dict:
    """What the scrape clock is doing, for /health and the console.

    in_flight_seconds is the honest answer to "why has data/books.json not
    moved" -- a batch job that started four minutes ago has not failed.
    """
    thread, started = _SCRAPE["thread"], _SCRAPE["started_at"]
    in_flight = bool(thread is not None and thread.is_alive())
    return {
        "in_flight": in_flight,
        "in_flight_seconds": round(time.time() - started, 1) if (in_flight and started) else None,
        "interval_seconds": scrape_interval(),
        "last_started": _SCRAPE["last_started"],
        "runs": _SCRAPE["runs"],
        "ticks_skipped_while_in_flight": _SCRAPE["skipped"],
        "timeout_seconds": _scrape_timeout(),
    }


def _scrape_timeout() -> int:
    from forge import brightdata

    return brightdata.HARD_TIMEOUT_SECONDS


def scrape_interval() -> float:
    """Seconds between scrapes. The watcher owns it, the environment overrides.

    Deliberately NOT AUDIT_INTERVAL_SECONDS. A batch run takes minutes; starting
    one every audit tick queued work faster than Bright Data could finish it.
    """
    if os.getenv("SCRAPE_INTERVAL_SECONDS"):
        return float(config.SCRAPE_INTERVAL_SECONDS)
    try:
        from forge import brightdata

        configured = (brightdata.watcher().get("run") or {}).get("interval_seconds")
    except Exception:
        configured = None
    try:
        return float(configured) if configured else float(config.SCRAPE_INTERVAL_SECONDS)
    except (TypeError, ValueError):
        return float(config.SCRAPE_INTERVAL_SECONDS)


def scrape_in_flight() -> bool:
    thread = _SCRAPE["thread"]
    return bool(thread is not None and thread.is_alive())


def scrape_due() -> bool:
    last = _SCRAPE["last_started"]
    return last is None or (time.time() - last) >= scrape_interval()


def start_scrape(force: bool = False) -> dict:
    """Start a scrape in its own thread, or say why one was not started.

    NEVER blocks and never raises. The caller -- an audit tick -- carries on
    immediately, which is the whole point: a batch job that takes minutes must
    not hold up a check suite that takes seconds.
    """
    if scrape_in_flight():
        _SCRAPE["skipped"] += 1
        held = round(time.time() - (_SCRAPE["started_at"] or time.time()), 1)
        log.info(
            "scrape still in flight after %ss -- skipping this tick's scrape and auditing anyway",
            held,
        )
        return {"started": False, "reason": "in_flight", "in_flight_seconds": held}

    if not force and not scrape_due():
        due_in = round(scrape_interval() - (time.time() - (_SCRAPE["last_started"] or 0)), 1)
        return {"started": False, "reason": "not_due", "due_in_seconds": max(due_in, 0.0)}

    def _work() -> None:
        try:
            scrape_once()  # looked up at call time so tests can substitute it
        except Exception:  # scrape_once already swallows everything; belt and braces
            log.exception("scrape thread died")
        finally:
            _SCRAPE["started_at"] = None

    thread = threading.Thread(target=_work, name="forge-scrape", daemon=True)
    _SCRAPE.update({"thread": thread, "started_at": time.time(),
                    "last_started": time.time(), "runs": _SCRAPE["runs"] + 1})
    thread.start()
    log.info("scrape started in the background (timeout %ss, next due in %ss)",
             _scrape_timeout(), int(scrape_interval()))
    return {"started": True, "reason": "due"}


def await_scrape(timeout: float = 30.0) -> bool:
    """Join the in-flight scrape. For tests and scripts ONLY -- never a tick."""
    thread = _SCRAPE["thread"]
    if thread is None:
        return True
    thread.join(timeout)
    return not thread.is_alive()


def scrape_once() -> dict:
    """One scrape, inside span forge.scrape. NEVER raises.

    A third-party site being slow or down must not stop the factory auditing the
    app it built. Every failure is logged at ERROR with the exception, recorded
    on the span and as a metric, and the previous data/books.json is left exactly
    as it was -- which is what lets D1 and D2 report it as a finding on the next
    pass instead of the data silently vanishing.
    """
    from forge import brightdata, store, telemetry

    watcher = brightdata.watcher()
    outcome = {"ok": False, "rows": 0, "reason": None, "wrote": False}

    with telemetry.stage_span("forge.scrape", "scrape") as span:
        def tag(**kw):
            if span is None:
                return
            for k, v in kw.items():
                try:
                    span.set_attribute(k, v)
                except Exception:
                    pass

        tag(**{"scrape.watcher": watcher.get("name", "books"),
               "scrape.collector_id": watcher.get("collector_id", "none")})
        try:
            rows = brightdata.scraper_run()
            validated = brightdata.validate_contract(rows)
            store.write_scrape(watcher, validated, contract_ok=True)
            outcome.update(ok=True, rows=len(validated), wrote=True)
            tag(**{"scrape.rows": len(validated), "scrape.contract_ok": True})
            log.info("scrape ok: %s row(s) written", len(validated))

        except brightdata.ContractViolation as exc:
            # Rows came back but are not usable. Keep the previous file.
            outcome["reason"] = str(exc)
            tag(**{"scrape.contract_ok": False, "scrape.error": str(exc)[:300]})
            telemetry.counter("forge_scrape_failures_total", 1, reason="contract")
            log.error("scrape contract failed, keeping previous data: %s", exc, exc_info=True)

        except Exception as exc:
            outcome["reason"] = str(exc)
            reason = "timeout" if exc.__class__.__name__ == "ScrapeTimeout" else "error"
            tag(**{"scrape.error": str(exc)[:300]})
            telemetry.counter("forge_scrape_failures_total", 1, reason=reason)
            log.error("scrape failed (%s), keeping previous data: %s",
                      exc.__class__.__name__, exc, exc_info=True)

        if not outcome["ok"] and span is not None:
            try:
                span.add_event("forge.scrape_failed", {"reason": (outcome["reason"] or "")[:300]})
            except Exception:
                pass

    _STATE["last_scrape"] = {"at": time.time(), **outcome}
    return outcome


async def run_once(trigger: str = "scheduled") -> dict:
    """One audit tick. Never raises."""
    if _STATE["running"]:
        log.info("audit already running, skipping this tick")
        return {"skipped": True, "reason": "overlap"}

    _STATE["running"] = True
    started = time.time()
    try:
        # The scrape is STARTED here and deliberately not waited on. Bright Data
        # falls back to a batch job on this target, so the scrape can outlive
        # several audit ticks -- joining it would stop the factory auditing the
        # app it built because a third party is slow. If one is still running,
        # this tick does not start another and audits anyway.
        scrape = start_scrape()

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
            "scrape": scrape,
        }
    except Exception as exc:
        _STATE.update({"failures": _STATE["failures"] + 1, "last_error": str(exc)})
        log.exception("audit tick failed")
        return {"error": str(exc)}
    finally:
        # Released here and nowhere else: an audit that raises must not leave
        # the guard latched, or every later tick skips itself as an overlap and
        # the factory goes quiet without a single error. Note this guards the
        # AUDIT only -- the scrape has its own in-flight check and its own
        # thread, so a batch job that outlives the tick never latches this.
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
        from forge import audit as audit_mod

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
    log.info(
        "audit scheduler started: audit every %ss against %s, scrape every %ss "
        "(batch job, %ss timeout, never on the audit's thread)",
        interval, config.PULSE_BASE_URL, int(scrape_interval()), _scrape_timeout(),
    )
    while True:
        try:
            summary = await run_once()
            if not summary.get("skipped"):
                log.info("audit: %s", summary)
        except Exception:
            log.exception("scheduler loop error")  # never let the loop die
        await asyncio.sleep(interval)

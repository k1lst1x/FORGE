from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from statistics import median
from typing import Any
from uuid import uuid4

from app.factory import engine, portal, scheduler, store
from app.factory.models import (
    FactoryRunStatus,
    FindingSeverity,
    IntakeType,
    TriageClassification,
)
from app.factory.project_record import ALERT_SPEC, PROJECT_RECORD, SIGNOZ_PANELS
from app.factory.scorecards import (
    ALERT_BELOW,
    ALERT_METRIC,
    PORT_SCORECARD,
    scorecards_for_findings,
    severity_totals,
    worst_score,
)

INJECT_TRIGGER = "inject"
DEFAULT_ROUTES = ["/", "/products"]

_INJECT_DEFS: dict[int, dict[str, Any]] = {
    1: {
        "title": "MODE 1 — security headers removed",
        "classification": TriageClassification.autofix_safe,
        "status": FactoryRunStatus.verifying,
        "findings": [
            {
                "check_id": "S1",
                "severity": FindingSeverity.high,
                "route": "/",
                "title": "Content-Security-Policy missing",
                "evidence": "GET / returned 200 with no Content-Security-Policy header.",
                "suggested_fix_hint": "Add security-headers middleware.",
            },
            {
                "check_id": "S2",
                "severity": FindingSeverity.high,
                "route": "/",
                "title": "X-Frame-Options missing",
                "evidence": "GET / returned 200 with no X-Frame-Options or CSP frame-ancestors.",
                "suggested_fix_hint": "Add security-headers middleware.",
            },
        ],
    },
    2: {
        "title": "MODE 2 — API-key-shaped string in a template",
        "classification": TriageClassification.false_positive,
        "status": FactoryRunStatus.escalated,
        "justification": (
            "S10 matched a Subresource Integrity hash / example key shape in a "
            "template comment. Written justification stored; no patch."
        ),
        "findings": [
            {
                "check_id": "S10",
                "severity": FindingSeverity.high,
                "route": "/products",
                "title": "Secret-shaped string in HTML",
                "evidence": "Template comment matched sk- followed by 20+ characters.",
                "suggested_fix_hint": "Remove, or suppress if this is an SRI hash.",
            }
        ],
    },
    3: {
        "title": "MODE 3 — /docs and /admin reachable",
        "classification": TriageClassification.needs_human_design,
        "status": FactoryRunStatus.escalated,
        "justification": (
            "Opening or closing /admin has blast radius we cannot reason about "
            "from outside. Escalated; no auto-patch."
        ),
        "findings": [
            {
                "check_id": "S12",
                "severity": FindingSeverity.medium,
                "route": "/docs",
                "title": "API docs endpoint reachable in production mode",
                "evidence": "GET /docs returned 200 with a full OpenAPI schema.",
                "suggested_fix_hint": "Guard /docs behind ENV == 'dev'.",
            },
            {
                "check_id": "S9",
                "severity": FindingSeverity.high,
                "route": "/admin",
                "title": "Sensitive path /admin reachable",
                "evidence": "GET /admin returned 200 with no auth challenge.",
                "suggested_fix_hint": "Add a route guard.",
            },
        ],
    },
    4: {
        "title": "MODE 4 — Pulse process stopped",
        "classification": TriageClassification.upstream_outage,
        "status": FactoryRunStatus.escalated,
        "outage": True,
        "justification": (
            "Audit target unreachable. Mass check failure is an outage, not 17 "
            "things to fix. Refusing to act."
        ),
        "findings": [],
    },
}


def snapshot(verified_within_hour: bool = False) -> dict[str, Any]:
    all_findings = store.list_findings()
    runs = store.list_runs()
    open_findings = _open_findings(all_findings, runs)
    cards = scorecards_for_findings(
        open_findings,
        routes=DEFAULT_ROUTES,
        tests_passing=True,
        verified_within_hour=verified_within_hour,
    )
    outage = _active_outage(runs)
    worst = worst_score(cards)
    would_fire = (not outage) and worst < ALERT_BELOW

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "scheduler": scheduler.status(),
        "alert": {
            **ALERT_SPEC,
            "metric": ALERT_METRIC,
            "threshold": ALERT_BELOW,
            "worst_score": worst,
            "would_fire": would_fire,
            "suppressed_reason": (
                "UPSTREAM_OUTAGE — scheduler must not open fix runs while Pulse is down."
                if outage
                else None
            ),
        },
        "scorecard_rules": PORT_SCORECARD,
        "scorecards": cards,
        "project": PROJECT_RECORD,
        "signoz_panels": SIGNOZ_PANELS,
        "panels": {
            "security_grade": {
                "title": SIGNOZ_PANELS[0]["title"],
                "routes": cards,
                "history": store.list_grade_history(),
            },
            "open_findings": {
                "title": SIGNOZ_PANELS[1]["title"],
                "by_severity": severity_totals(open_findings),
                "items": [finding.model_dump() for finding in all_findings[:40]],
            },
            "triage": {
                "title": SIGNOZ_PANELS[2]["title"],
                "by_classification": _triage_counts(runs),
            },
            "fix_outcomes": {
                "title": SIGNOZ_PANELS[3]["title"],
                "by_result": _outcome_counts(runs),
            },
            "audit": {
                "title": SIGNOZ_PANELS[4]["title"],
                "p50_ms": _percentile(_run_durations_ms(runs), 50),
                "p95_ms": _percentile(_run_durations_ms(runs), 95),
                "recent_errors": _recent_errors(runs),
            },
        },
        "outage": outage,
    }


def inject(mode: int) -> dict[str, Any]:
    if mode not in _INJECT_DEFS:
        raise ValueError("mode must be 1, 2, 3, or 4")

    spec = _INJECT_DEFS[mode]
    run_id = f"inject_{mode}_{uuid4().hex[:8]}"
    store.create_run(
        run_id=run_id,
        title=spec["title"],
        brief=spec["title"],
        trigger=INJECT_TRIGGER,
        intake=IntakeType.finding,
    )
    store.update_run(
        run_id,
        status=spec["status"],
        classification=spec["classification"],
        outcome=spec.get("justification") or spec["title"],
        next_gate=spec.get("justification"),
    )

    for finding in spec["findings"]:
        store.save_finding(
            finding_id=f"inj_{uuid4().hex[:10]}",
            run_id=run_id,
            check_id=finding["check_id"],
            severity=finding["severity"],
            route=finding["route"],
            title=finding["title"],
            evidence=finding["evidence"],
            suggested_fix_hint=finding["suggested_fix_hint"],
            metadata={"inject_mode": mode},
        )

    if spec.get("outage"):
        store.record_outage(active=True, run_id=run_id, justification=spec["justification"])
    else:
        _snapshot_grades(source=INJECT_TRIGGER)
    publish_scorecards()

    return {
        "mode": mode,
        "run_id": run_id,
        "classification": spec["classification"].value,
        "outage": bool(spec.get("outage")),
        "observability": snapshot(),
    }


def restore() -> dict[str, Any]:
    deleted = store.delete_runs_by_trigger(INJECT_TRIGGER)
    store.record_outage(active=False, run_id=None, justification=None)
    store.delete_grade_history(source=INJECT_TRIGGER)
    _snapshot_grades(source="audit")
    publish_scorecards()
    return {"restored": True, "deleted_runs": deleted, "observability": snapshot()}


def ingest_alert(payload: dict[str, Any]) -> list[str]:
    alerts = payload.get("alerts")
    if not isinstance(alerts, list) or not alerts:
        alerts = [payload]

    run_ids: list[str] = []
    for alert in alerts:
        labels = alert.get("labels") if isinstance(alert.get("labels"), dict) else {}
        annotations = (
            alert.get("annotations") if isinstance(alert.get("annotations"), dict) else {}
        )
        route = str(labels.get("route") or "/")
        finding = {
            "check_id": str(labels.get("check_id") or "S1"),
            "severity": "HIGH",
            "route": route,
            "title": str(
                annotations.get("summary") or "Security grade dropped below Silver"
            ),
            "evidence": str(
                annotations.get("description")
                or f"{ALERT_METRIC} fell below {ALERT_BELOW} on {route}"
            ),
        }
        cr = engine.run_from_finding(finding=finding, trigger="signoz")
        run_ids.append(cr.run_id)
    _snapshot_grades(source="alert")
    publish_scorecards()
    return run_ids


def publish_scorecards() -> None:
    snap = snapshot()
    for card in snap["scorecards"]:
        findings = [
            {"severity": "HIGH"} for _ in range(int(card["open_findings_high"]))
        ] + [
            {"severity": "MED"} for _ in range(int(card["open_findings_med"]))
        ]
        portal.update_scorecard(card["route"], card["grade"], findings)
        telemetry_gauge = int(card["score"])
        from app.factory import telemetry

        telemetry.counter(
            "forge_security_grade",
            telemetry_gauge,
            route=card["route"],
            grade=card["grade"],
        )


def maybe_trigger_fix() -> list[str]:
    snap = snapshot()
    if snap["outage"] or not snap["alert"]["would_fire"]:
        return []
    if _fix_already_in_flight():
        return []
    firing = [
        card for card in snap["scorecards"] if card["alert_would_fire"]
    ]
    if not firing:
        return []
    worst = firing[0]
    return ingest_alert(
        {
            "labels": {"route": worst["route"], "severity": "critical"},
            "annotations": {
                "summary": f"Security grade dropped to {worst['grade']} on {worst['route']}",
                "description": f"{ALERT_METRIC} is {worst['score']}, below {ALERT_BELOW}.",
            },
        }
    )


def publish_after_audit() -> dict[str, Any]:
    _snapshot_grades(source="audit")
    publish_scorecards()
    maybe_trigger_fix()
    return snapshot()


def _fix_already_in_flight() -> bool:
    in_flight = {
        FactoryRunStatus.planned,
        FactoryRunStatus.gathering_context,
        FactoryRunStatus.triaging,
        FactoryRunStatus.acting,
        FactoryRunStatus.verifying,
        FactoryRunStatus.awaiting_human,
    }
    return any(
        run.trigger == "signoz" and run.status in in_flight for run in store.list_runs()
    )


def _snapshot_grades(source: str) -> None:
    runs = store.list_runs()
    findings = _open_findings(store.list_findings(), runs)
    for card in scorecards_for_findings(findings, routes=DEFAULT_ROUTES):
        store.save_grade_snapshot(
            route=card["route"],
            grade=card["grade"],
            score=card["score"],
            high_count=card["open_findings_high"],
            med_count=card["open_findings_med"],
            source=source,
        )


def _open_findings(findings: list[Any], runs: list[Any]) -> list[Any]:
    ignored = {
        TriageClassification.false_positive,
        TriageClassification.upstream_outage,
        TriageClassification.duplicate,
    }
    runs_by_id = {run.id: run for run in runs}
    open_items = []
    for finding in findings:
        run = runs_by_id.get(finding.run_id)
        if run is not None and run.classification in ignored:
            continue
        open_items.append(finding)
    return open_items


def _active_outage(runs: list[Any]) -> bool:
    recorded = store.outage_state()
    if recorded.get("active"):
        return True
    return any(
        run.classification == TriageClassification.upstream_outage
        and run.trigger == INJECT_TRIGGER
        for run in runs
    )


def _triage_counts(runs: list[Any]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for classification in TriageClassification:
        counts[classification.value] = 0
    for run in runs:
        if run.classification is not None:
            counts[run.classification.value] += 1
    return dict(counts)


def _outcome_counts(runs: list[Any]) -> dict[str, int]:
    attempted = 0
    verified = 0
    rejected = 0
    escalated = 0
    for run in runs:
        attempted += 1
        if run.status == FactoryRunStatus.released:
            verified += 1
        elif run.status == FactoryRunStatus.failed:
            rejected += 1
        elif run.status == FactoryRunStatus.escalated:
            escalated += 1
    return {
        "attempted": attempted,
        "verified": verified,
        "rejected": rejected,
        "escalated": escalated,
    }


def _run_durations_ms(runs: list[Any]) -> list[float]:
    durations: list[float] = []
    for run in runs:
        try:
            start = datetime.fromisoformat(run.created_at)
            end = datetime.fromisoformat(run.updated_at)
        except ValueError:
            continue
        delta = (end - start).total_seconds() * 1000
        if delta >= 0:
            durations.append(delta)
    return durations


def _percentile(values: list[float], pct: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if pct >= 95 and len(ordered) > 1:
        return round(ordered[-1], 2)
    return round(float(median(ordered)), 2)


def _recent_errors(runs: list[Any]) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    scheduler_error = scheduler.status().get("last_error")
    if isinstance(scheduler_error, str) and scheduler_error:
        errors.append({"source": "scheduler", "message": scheduler_error})
    for run in runs:
        if run.status in {FactoryRunStatus.failed, FactoryRunStatus.escalated}:
            errors.append(
                {
                    "source": run.id,
                    "message": run.outcome or run.next_gate or run.status.value,
                }
            )
        if len(errors) >= 8:
            break
    return errors

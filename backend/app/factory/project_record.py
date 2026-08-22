"""Port project record — named judging criterion. Paste into Port as-is."""

PROJECT_RECORD = {
    "identifier": "forge",
    "title": "FORGE",
    "blueprint": "project",
    "properties": {
        "goal": (
            "A factory that builds web features and continuously audits and "
            "repairs its own output, with a human approving every change."
        ),
        "technical_choices": (
            "FastAPI for async webhooks and native OTel; single-file route "
            "generation for reliable codegen; audit policy as the acceptance "
            "criteria for both loops; the factory can write to the app but "
            "not to itself."
        ),
        "known_risks": (
            "Alert grouping delays detection, mitigated by scheduler-side "
            "checks; single triage agent with no critic; audit checks are "
            "hand-written rather than derived from a standard; generated "
            "code is reviewed by a human but not by a second model."
        ),
        "cataloged_services": [
            "forge-control",
            "pulse",
            "cloudflared",
        ],
    },
}

ALERT_SPEC = {
    "name": "forge-security-grade-below-silver",
    "type": "METRIC_BASED",
    "query": "forge_security_grade",
    "aggregation": "min",
    "window": "1m",
    "condition": "below 2",
    "evaluate_every": "1m",
    "labels": {"severity": "critical"},
    "note": (
        "Give each route a distinct alert name so SigNoz groups do not merge. "
        "SigNoz webhook grouping is ~5 minutes; the scheduler also checks "
        "grades every 60s and POSTs the same /factory/intake/finding handler."
    ),
    "channel": {
        "type": "webhook",
        "path": "/factory/intake/finding",
        "auth": "basic — get username/password from Damir",
    },
}

SIGNOZ_PANELS = [
    {
        "id": 1,
        "metric": "forge_security_grade",
        "title": "Security grade over time, per route",
        "why": "Injection is a visible cliff; the fix is a visible recovery.",
    },
    {
        "id": 2,
        "metric": "forge_findings_open",
        "title": "Open findings by severity, stacked",
        "why": "App posture at a glance.",
    },
    {
        "id": 3,
        "metric": "forge_triage_total",
        "title": "Triage decisions by classification",
        "why": "Shows the system reasoning, not just running.",
    },
    {
        "id": 4,
        "metric": "forge_fix_outcome_total",
        "title": "Fix outcomes — attempted / verified / rejected / escalated",
        "why": "Auto-repair as a first-class signal.",
    },
    {
        "id": 5,
        "metric": "forge_audit_duration_ms",
        "title": "Audit duration p50/p95 + recent error logs",
        "why": "Click a log line, jump to the trace — do that on camera.",
    },
]

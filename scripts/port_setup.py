"""
scripts/port_setup.py -- create the blueprints in Port.

Run once. Without these, every entity write 404s -- Port does not say "no such
blueprint", it just returns 404 as though the data were missing, which is
exactly the error that made the control plane look broken.

    python scripts/port_setup.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BLUEPRINTS = [
    {
        "identifier": "page",
        "title": "Pulse Page",
        "icon": "Service",
        "schema": {"properties": {
            "route": {"type": "string", "title": "Route"},
            "grade": {"type": "string", "title": "Grade", "enum": ["gold", "silver", "bronze"]},
            "open_findings_high": {"type": "number", "title": "Open HIGH findings"},
            "open_findings_med": {"type": "number", "title": "Open MED findings"},
            "open_findings": {"type": "number", "title": "Open findings"},
            "last_audited": {"type": "string", "title": "Last audited"},
        }, "required": []},
    },
    {
        "identifier": "finding",
        "title": "Audit Finding",
        "icon": "Alert",
        "schema": {"properties": {
            "check_id": {"type": "string", "title": "Check"},
            "severity": {"type": "string", "title": "Severity", "enum": ["HIGH", "MED", "LOW"]},
            "route": {"type": "string", "title": "Route"},
            "evidence": {"type": "string", "title": "Evidence"},
            "status": {"type": "string", "title": "Status"},
            "justification": {"type": "string", "title": "Justification"},
            "occurrences": {"type": "number", "title": "Occurrences"},
        }, "required": []},
    },
    {
        "identifier": "factory_run",
        "title": "Factory Run",
        "icon": "Deployment",
        "schema": {"properties": {
            "intake": {"type": "string", "title": "Intake", "enum": ["brief", "finding"]},
            "stage": {"type": "string", "title": "Stage"},
            "status": {"type": "string", "title": "Status"},
            "trace_id": {"type": "string", "title": "Trace ID"},
            "classification": {"type": "string", "title": "Triage"},
            "should_act": {"type": "boolean", "title": "Acted"},
            "pr_url": {"type": "string", "title": "Pull request"},
            "files_changed": {"type": "array", "title": "Files changed"},
            "tests_passed": {"type": "boolean", "title": "Tests passed"},
            "duration_ms": {"type": "number", "title": "Duration (ms)"},
            "justification": {"type": "string", "title": "Justification"},
        }, "required": []},
    },
    {
        "identifier": "project",
        "title": "Project",
        "icon": "Book",
        "schema": {"properties": {
            "goal": {"type": "string", "title": "Goal"},
            "technical_choices": {"type": "string", "title": "Technical choices"},
            "known_risks": {"type": "string", "title": "Known risks"},
        }, "required": []},
    },
]


def main() -> int:
    from forge import portal

    if not portal.configured():
        print("\n  PORT_CLIENT_ID / PORT_CLIENT_SECRET are not set.\n")
        return 2

    token, host = portal.token()
    if not token:
        print("\n  Could not authenticate against Port on any host.\n")
        return 2
    print(f"\n  authenticated against {host}\n")

    for blueprint in BLUEPRINTS:
        existing = portal._request("GET", f"/v1/blueprints/{blueprint['identifier']}")
        if existing is not None and existing.status_code == 200:
            response = portal._request("PUT", f"/v1/blueprints/{blueprint['identifier']}", json=blueprint)
            verb = "updated"
        else:
            response = portal._request("POST", "/v1/blueprints", json=blueprint)
            verb = "created"
        ok = response is not None and response.status_code < 300
        detail = "" if ok else f" -- {response.status_code if response is not None else 'no response'}"
        print(f"  {'ok  ' if ok else 'FAIL'} {verb:8} {blueprint['identifier']}{detail}")
        if not ok and response is not None:
            print(f"       {response.text[:200]}")

    print("\n  Port blueprints ready. Runs, pages and findings will now upsert.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

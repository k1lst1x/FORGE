"""
scripts/demo_run.py -- drive the engine by hand.

    python scripts/demo_run.py --brief "Add a page showing out-of-stock items"
    python scripts/demo_run.py --finding autofix
    python scripts/demo_run.py --finding cors      # declines: blast radius
    python scripts/demo_run.py --finding sri       # declines: false positive
    python scripts/demo_run.py --finding outage    # declines: app is down
    python scripts/demo_run.py --all               # every path, one after another

Until Damir's modules are real this runs entirely against the stubs, which is
the point: the engine is finished and testable before anything under it is.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FINDINGS = {
    "autofix": {
        "finding_id": "f_7a3c",
        "check_id": "S9",
        "severity": "HIGH",
        "route": "/stock-alerts",
        "title": "API documentation endpoint reachable in production mode",
        "evidence": "GET /docs returned 200 with a full OpenAPI schema listing 11 endpoints",
        "first_seen": "2026-08-22T14:07:12Z",
        "occurrences": 3,
        "suggested_fix_hint": "Guard the docs route behind settings.ENV == dev",
        "page_source": "<html><head><title>Stock alerts</title></head><body>...</body></html>",
    },
    "cors": {
        "finding_id": "f_9b21",
        "check_id": "S8",
        "severity": "HIGH",
        "route": "/products",
        "title": "CORS allows any origin while credentials are permitted",
        "evidence": "Access-Control-Allow-Origin: * with Access-Control-Allow-Credentials: true",
        "suggested_fix_hint": "Restrict the origin allowlist",
        "page_source": "<html><body>Products</body></html>",
    },
    "sri": {
        "finding_id": "f_4d02",
        "check_id": "S10",
        "severity": "HIGH",
        "route": "/",
        "title": "Secret-shaped string found in HTML",
        "evidence": 'script tag carries integrity="sha384-oqVuAfXRKap7fdgcCY5uykM6+R9GqQ8K"',
        "suggested_fix_hint": "Remove the secret, move it to env",
        "page_source": "<html><body>Home</body></html>",
    },
    "outage": {
        "finding_id": "f_0000",
        "check_id": "S1",
        "severity": "HIGH",
        "route": "/products",
        "title": "Content-Security-Policy header missing",
        "evidence": "Connection refused on http://localhost:8100/products",
        "suggested_fix_hint": "Add security-headers middleware",
        "reachable": False,
        "page_source": "",
    },
}
FINDINGS["retry"] = dict(FINDINGS["autofix"], finding_id="f_retry", route="/retry-demo")


def show(cr) -> None:
    print("\n" + "=" * 74)
    for key, value in cr.summary().items():
        if value in (None, [], {}, ""):
            continue
        if key == "justification":
            print(f"  {key:>14}: {value[:180]}")
        else:
            print(f"  {key:>14}: {value}")
    print("=" * 74 + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Drive the FORGE engine")
    parser.add_argument("--brief", help="run Loop A with this brief text")
    parser.add_argument("--finding", choices=sorted(FINDINGS), help="run Loop B with a canned finding")
    parser.add_argument("--all", action="store_true", help="run every path")
    parser.add_argument("--json", action="store_true", help="dump the ChangeRequest as JSON")
    args = parser.parse_args()

    from forge import engine

    runs = []
    if args.all:
        runs.append(("brief", engine.run_from_brief("Add a page showing out-of-stock products, sorted by price descending.")))
        for name in ("autofix", "cors", "sri", "outage"):
            runs.append((name, engine.run_from_finding(FINDINGS[name])))
        runs.append(("retry", engine.run_from_finding(FINDINGS["retry"])))
    elif args.brief:
        runs.append(("brief", engine.run_from_brief(args.brief)))
    elif args.finding:
        runs.append((args.finding, engine.run_from_finding(FINDINGS[args.finding])))
    else:
        parser.print_help()
        return 1

    for name, cr in runs:
        print("\n### " + name)
        show(cr)
        if args.json:
            print(json.dumps(cr.summary(), indent=2, default=str))

    print("\nsummary")
    for name, cr in runs:
        print(f"  {name:>8}  {str(cr.classification):>18}  act={str(cr.should_act):<5}  outcome={cr.outcome}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

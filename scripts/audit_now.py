"""
scripts/audit_now.py -- run the audit once and print what it found.

    python scripts/audit_now.py                          # the routes in the registry
    python scripts/audit_now.py --url http://127.0.0.1:8199
    python scripts/audit_now.py --routes / /products /security
    python scripts/audit_now.py --json                   # for piping

This is what `make audit` runs, and it is the terminal screen in the video, so
it prints the grade per route in large plain type and the findings sorted worst
first. Read it top to bottom and you know the security posture of the app.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SEV_ORDER = {"HIGH": 0, "MED": 1, "LOW": 2}


def _clean_route(raw: str) -> str:
    """Undo Git Bash path mangling on Windows.

    MSYS rewrites an argument that looks like a unix path into a Windows one,
    so `--routes / /products` arrives as `C:/Program Files/Git/` and
    `C:/Program Files/Git/products`. A route can never legitimately start with
    a drive letter, so when one does, strip the longest prefix that is a real
    directory on disk -- what remains is the route that was typed.

    The alternative is MSYS_NO_PATHCONV=1 in front of every command, which
    someone will forget at 16:00.
    """
    import os
    import re

    if not re.match(r"^[A-Za-z]:[\/]", raw):
        return raw if raw.startswith("/") else "/" + raw
    normalised = raw.replace("\\", "/")
    cut = 0
    for i, char in enumerate(normalised):
        if char == "/" and os.path.isdir(normalised[: i + 1]):
            cut = i
    return normalised[cut:] or "/"
GRADE_MARK = {"gold": "GOLD  ", "silver": "SILVER", "bronze": "BRONZE"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the FORGE audit once")
    parser.add_argument("--url", default=None, help="base url (default: PULSE_BASE_URL)")
    parser.add_argument("--routes", nargs="*", default=None, help="routes to audit")
    parser.add_argument("--json", action="store_true", help="print findings as JSON")
    parser.add_argument("--quiet", action="store_true", help="suppress the span tree")
    args = parser.parse_args()

    if args.quiet:
        import os

        os.environ["FORGE_TRACE_CONSOLE"] = "0"

    from forge import audit, config

    base_url = args.url or config.PULSE_BASE_URL
    routes = [_clean_route(r) for r in args.routes] if args.routes else ["/", "/products"]
    result = audit.run_audit(base_url, routes)

    if args.json:
        print(json.dumps({"summary": result.as_dict(), "findings": result.findings}, indent=2))
        return 0 if not result.findings_high else 1

    print()
    print("  AUDIT  " + base_url + "   " + str(result.duration_ms) + "ms")
    if not result.reachable:
        print("  TARGET UNREACHABLE -- every check failed because nothing was served.")
        print("  This is an outage, not a defect list. Triage will back off.")
    print()
    for route, grade in result.grades.items():
        counts = {}
        for f in result.for_route(route):
            counts[f["severity"]] = counts.get(f["severity"], 0) + 1
        tally = "  ".join(f"{n} {s}" for s, n in sorted(counts.items(), key=lambda kv: SEV_ORDER[kv[0]]))
        print(f"  [{GRADE_MARK.get(grade, grade)}]  {route:<24} {tally or 'clean'}")
    print()

    ordered = sorted(result.findings, key=lambda f: (SEV_ORDER[f["severity"]], f["route"], f["check_id"]))
    for f in ordered:
        print(f'  {f["severity"]:<4} {f["check_id"]:<4} {f["route"]:<16} {f["title"]}')
        print(f'       {f["evidence"][:150]}')
        print(f'       fix: {f["suggested_fix_hint"]}')
        print()

    print(f"  {len(result.findings)} findings, {len(result.findings_high)} HIGH, worst grade {result.worst_grade}")
    print()
    return 0 if not result.findings_high else 1


if __name__ == "__main__":
    raise SystemExit(main())

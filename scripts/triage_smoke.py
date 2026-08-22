"""
scripts/triage_smoke.py -- exercise all five classifications against the real API.

    export ANTHROPIC_API_KEY=...
    python scripts/triage_smoke.py
    python scripts/triage_smoke.py --repeat 10      # is it reliable, or just lucky?
    python scripts/triage_smoke.py --case sri       # one case, verbose

The two guard cases pass with no credentials at all. The other four are real
model calls and are the ones to run ten times, not once -- the plan is explicit
that FALSE_POSITIVE and UPSTREAM_OUTAGE have to be reliable, and repeat is how
you find out which they are.

Exit code is non-zero if any case did not land on its expected classification,
so this can gate a commit.
"""
from __future__ import annotations

import argparse
import collections
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SRI = "sha384-oqVuAfXRKap7fdgcCY5uykM6+R9GqQ8K/uxy9rx7HNQlGYl1kPzQho1wx4JwY8wC"
PAGE = "<html><head><title>Pulse</title></head><body><h1>Pulse</h1><p>Widget A $49.00</p></body></html>"
MAIN = "from fastapi import FastAPI\napp = FastAPI()\n\n@app.get('/products')\ndef products():\n    return render('products.html')\n"

CASES = {
    "outage": {
        "expect": "UPSTREAM_OUTAGE",
        "finding": {"finding_id": "f_out", "check_id": "S1", "severity": "HIGH", "route": "/products",
                    "title": "Content-Security-Policy present", "evidence": "Connection refused",
                    "suggested_fix_hint": "Add security-headers middleware", "reachable": False},
        "page": "", "files": {}, "history": [],
    },
    "duplicate": {
        "expect": "DUPLICATE",
        "finding": {"finding_id": "f_dup", "check_id": "S9", "severity": "HIGH", "route": "/products",
                    "title": "Sensitive paths unreachable", "evidence": "GET /admin returned 200",
                    "suggested_fix_hint": "Add a route guard"},
        "page": PAGE, "files": {"pulse/main.py": MAIN},
        "history": [{"check_id": "S9", "route": "/products", "status": "in_flight", "run_id": "run_inflight"}],
    },
    "autofix": {
        "expect": "AUTOFIX_SAFE",
        "finding": {"finding_id": "f_fix", "check_id": "S12", "severity": "MED", "route": "/products",
                    "title": "API documentation endpoint not exposed in production mode",
                    "evidence": "GET /docs returned 200 with an OpenAPI schema listing 11 endpoints",
                    "suggested_fix_hint": "Guard the docs route behind settings.ENV == dev"},
        "page": PAGE, "files": {"pulse/main.py": MAIN}, "history": [],
    },
    "cors": {
        "expect": "NEEDS_HUMAN_DESIGN",
        "finding": {"finding_id": "f_cors", "check_id": "S8", "severity": "HIGH", "route": "/products",
                    "title": "CORS not wildcard when credentials are allowed",
                    "evidence": "Access-Control-Allow-Origin: * with Access-Control-Allow-Credentials: true",
                    "suggested_fix_hint": "Restrict the origin allowlist"},
        "page": PAGE, "files": {"pulse/main.py": MAIN}, "history": [],
    },
    "sri": {
        "expect": "FALSE_POSITIVE",
        "finding": {"finding_id": "f_sri", "check_id": "S10", "severity": "HIGH", "route": "/products",
                    "title": "No secret-shaped strings in HTML or inline JS",
                    "evidence": f'a base64 blob of 40+ chars at offset 122 in ...chart.js" integrity="{SRI[:8]}...[redacted, 64 chars]" crossorigin="anonymous"...',
                    "suggested_fix_hint": "Remove the value from the template"},
        "page": f'<html><head><script src="/chart.js" integrity="{SRI}" crossorigin="anonymous"></script></head><body><h1>Pulse</h1></body></html>',
        "files": {"pulse/templates/base.html": f'<script src="/chart.js" integrity="{SRI}"></script>'},
        "history": [],
    },
    "brief": {
        "expect": "NEW_FEATURE",
        "finding": {"check_id": "BRIEF", "severity": "NONE", "route": None, "title": "Out of stock page",
                    "evidence": "Add a page showing only out-of-stock products, sorted by price descending."},
        "page": "", "files": {"pulse/routes/products.py": MAIN}, "history": [],
    },
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Exercise every triage path")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--case", choices=sorted(CASES))
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    if args.quiet:
        os.environ["FORGE_TRACE_CONSOLE"] = "0"

    from forge import triage

    if not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")):
        print("\n  WARNING: no ANTHROPIC_API_KEY set. The two guard cases are still real;")
        print("  every other case will run on policy heuristics, not model triage.\n")

    names = [args.case] if args.case else list(CASES)
    tally: dict[str, collections.Counter] = {n: collections.Counter() for n in names}
    failures = 0

    for run in range(args.repeat):
        for name in names:
            case = CASES[name]
            result = triage.classify(case["finding"], case["page"], case["files"], case["history"])
            tally[name][result.classification] += 1
            ok = result.classification == case["expect"]
            failures += 0 if ok else 1
            if args.repeat == 1 or not ok:
                mark = "ok  " if ok else "FAIL"
                print(f'\n  [{mark}] {name:<10} -> {result.classification}  '
                      f'(expected {case["expect"]}, by {result.decided_by}, conf {result.confidence})')
                print(f'         {result.justification[:220]}')
                if result.tokens_in:
                    print(f'         tokens in/out: {result.tokens_in}/{result.tokens_out}')

    if args.repeat > 1:
        print(f"\n  stability over {args.repeat} runs\n")
        for name in names:
            counts = ", ".join(f"{c} x{n}" for c, n in tally[name].most_common())
            hits = tally[name][CASES[name]["expect"]]
            print(f'  {name:<10} {hits}/{args.repeat} as expected   {counts}')

    print(f"\n  {failures} unexpected classification(s)\n")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

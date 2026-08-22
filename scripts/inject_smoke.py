"""
scripts/inject_smoke.py -- is mode 4 reliable, or only usually?

    python scripts/inject_smoke.py --repeat 10
    python scripts/inject_smoke.py --repeat 10 --spawn tests.fixtures.insecure_app:app --port 8199

Each iteration runs the whole chain a judge will watch:

    stop Pulse -> the port really closes
               -> a fresh audit reports the target unreachable
               -> triage classifies it UPSTREAM_OUTAGE and declines to act
               -> restore brings Pulse back on its own command line

Any iteration that misses any step is a failure, and the exit code says so.
This is the video's best moment; it has to be reliable, not lucky.
"""
from __future__ import annotations

import argparse
import collections
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    parser = argparse.ArgumentParser(description="Exercise defect injection mode 4")
    parser.add_argument("--repeat", type=int, default=10)
    parser.add_argument("--url", default=None, help="target base url (default: PULSE_BASE_URL)")
    parser.add_argument("--spawn", default=None, help="ASGI path to start if the target is down")
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    os.environ["FORGE_TRACE_CONSOLE"] = "0"
    if args.url:
        os.environ["PULSE_BASE_URL"] = args.url

    from forge import audit, config, inject, triage

    url = args.url or config.PULSE_BASE_URL
    port = args.port or urlparse(url).port or 80

    def ensure_up() -> bool:
        if inject._port_open(port):
            return True
        if not args.spawn:
            return False
        subprocess.Popen(
            [sys.executable, "-m", "uvicorn", args.spawn, "--host", "127.0.0.1",
             "--port", str(port), "--log-level", "warning"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + 20
        while time.time() < deadline and not inject._port_open(port):
            time.sleep(0.2)
        return inject._port_open(port)

    if not ensure_up():
        print(f"\n  target {url} is not running and could not be started.")
        print("  start Pulse, or pass --spawn tests.fixtures.insecure_app:app --port 8199\n")
        return 2

    print(f"\n  mode 4 against {url}, {args.repeat} iterations\n")
    tally = collections.Counter()
    failures = []

    for run in range(1, args.repeat + 1):
        steps = {}
        try:
            inject.inject(4)
            steps["stopped"] = not inject._port_open(port)

            result = audit.run_audit(url, ["/"])
            steps["audit_unreachable"] = result.reachable is False
            steps["findings_produced"] = len(result.findings) > 0

            classification = "NONE"
            if result.findings:
                decision = triage.classify(result.findings[0], "", {}, [])
                classification = decision.classification
                steps["triage_outage"] = classification == "UPSTREAM_OUTAGE"
                steps["declined"] = decision.should_act is False
            tally[classification] += 1

            restored = inject.restore()
            steps["restarted"] = restored["pulse_restarted"] and inject._port_open(port)
        except Exception as exc:
            steps["error"] = f"{type(exc).__name__}: {exc}"

        bad = [name for name, ok in steps.items() if ok is not True]
        mark = "ok  " if not bad else "FAIL"
        print(f"  [{mark}] {run:>2}/{args.repeat}  triage={tally.most_common(1)[0][0]:<16} "
              + ("" if not bad else "failed: " + ", ".join(f"{k}={steps[k]}" for k in bad)))
        if bad:
            failures.append(run)
            if not inject._port_open(port):
                ensure_up()  # leave the next iteration a target

    print(f"\n  outage classification: {tally['UPSTREAM_OUTAGE']}/{args.repeat}")
    for name, count in tally.most_common():
        if name != "UPSTREAM_OUTAGE":
            print(f"  UNEXPECTED {name}: {count}")
    print(f"  {len(failures)} failed iteration(s)"
          + (f": {failures}" if failures else "") + "\n")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

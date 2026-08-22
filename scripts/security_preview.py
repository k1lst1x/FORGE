"""
scripts/security_preview.py -- render /security to a file without running Pulse.

    python scripts/security_preview.py --url http://127.0.0.1:8199

Runs a real audit, feeds it through the real findings API and the real template,
and writes security_preview.html. Useful for checking the page reads at video
compression before the app is wired up, and for a quick look at posture without
opening a browser tab on the live app.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    parser = argparse.ArgumentParser(description="Render the security page from a live audit")
    parser.add_argument("--url", default=None, help="app to audit (default: PULSE_BASE_URL)")
    parser.add_argument("--routes", nargs="*", default=["/", "/products"])
    parser.add_argument("--out", default="security_preview.html")
    args = parser.parse_args()

    os.environ["FORGE_TRACE_CONSOLE"] = "0"
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from forge import audit, config
    from forge.status import router as forge_router
    from pulse.routes import security

    url = args.url or config.PULSE_BASE_URL
    result = audit.run_audit(url, args.routes)

    forge_app = FastAPI()
    forge_app.include_router(forge_router)
    payload = TestClient(forge_app).get("/api/findings").json()

    security._fetch = lambda: payload
    pulse_app = FastAPI()
    pulse_app.include_router(security.router)
    html = TestClient(pulse_app).get("/security").text
    Path(args.out).write_text(html, encoding="utf-8")

    print(f"\n  {url} -> {args.out}")
    print(f"  state: {security._state(payload)}")
    print(f"  {payload.get('open_count', 0)} open findings: {payload.get('totals')}")
    for row in payload.get("routes", []):
        print(f"  {row['grade'].upper():>6}  {row['route']}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

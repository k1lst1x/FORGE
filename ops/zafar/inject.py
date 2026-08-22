"""Rehearse Zafar inject modes against a running forge-control.

Usage:
    python ops/zafar/inject.py 1
    python ops/zafar/inject.py 4
    python ops/zafar/inject.py restore
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"


def post(path: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{BASE}{path}",
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    argument = sys.argv[1]
    try:
        if argument == "restore":
            body = post("/factory/restore")
        else:
            body = post("/factory/inject", {"mode": int(argument)})
    except urllib.error.URLError as exc:
        print(f"forge-control is not reachable on {BASE}: {exc}")
        return 1
    print(json.dumps(body, indent=2)[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
scripts/up.py -- start the whole factory with one command.

    python scripts/up.py        (or: make up)

Starts Pulse on 8100 and forge-control on 8000, labels their output, and shuts
both down cleanly on Ctrl-C. Prints what is and is not configured up front, so
nobody spends twenty minutes wondering why patches are not being written.
"""
from __future__ import annotations

import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from forge import config  # noqa: E402

PROCESSES: list[subprocess.Popen] = []


def _pump(process: subprocess.Popen, label: str) -> None:
    for line in iter(process.stdout.readline, ""):
        if line.strip():
            print(f"[{label}] {line.rstrip()}", flush=True)


def _start(label: str, command: list[str]) -> subprocess.Popen:
    process = subprocess.Popen(
        command, cwd=str(config.REPO_ROOT), stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, bufsize=1,
    )
    PROCESSES.append(process)
    threading.Thread(target=_pump, args=(process, label), daemon=True).start()
    return process


def _shutdown(*_):
    print("\nstopping...")
    for process in PROCESSES:
        if process.poll() is None:
            process.terminate()
    time.sleep(1.5)
    for process in PROCESSES:
        if process.poll() is None:
            process.kill()
    sys.exit(0)


def main() -> int:
    missing = [k for k, v in config.missing().items() if v]
    print("\n  FORGE")
    print(f"  pulse          http://localhost:8100")
    print(f"  forge-control  {config.FORGE_CONTROL_URL}   (console + approvals)")
    print(f"  security page  http://localhost:8100/security")
    print(f"  audit every    {config.AUDIT_INTERVAL_SECONDS}s against {config.AUDIT_ROUTES}")
    if missing:
        print(f"\n  NOT CONFIGURED: {', '.join(missing)}")
        if "llm" in missing:
            print("  -> no ANTHROPIC_API_KEY or OPENAI_API_KEY: triage runs on policy heuristics")
            print("     and the planner cannot write patches, so fix runs escalate instead.")
        if "github" in missing:
            print("  -> no GITHUB_TOKEN: changes land on a real local branch, no pull request.")
    print()

    signal.signal(signal.SIGINT, _shutdown)
    _start("pulse", [sys.executable, "-m", "uvicorn", "pulse.main:app",
                     "--host", "127.0.0.1", "--port", "8100", "--log-level", "warning"])
    time.sleep(3)
    _start("forge", [sys.executable, "-m", "forge.api"])

    try:
        while all(p.poll() is None for p in PROCESSES):
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    _shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

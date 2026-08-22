"""
forge/inject.py -- defect injection, so a judge can choose what breaks.

    python -m forge.inject 1|2|3|4     (or: make inject MODE=1)
    python -m forge.inject --restore
    python -m forge.inject --status

  1  Remove the security-headers middleware from pulse/main.py
  2  Add an API-key-shaped string into a template comment
  3  Un-guard the /docs route and add an /admin route with no auth
  4  Stop the Pulse process entirely  -> MUST classify as UPSTREAM_OUTAGE

WE HIDE NOTHING
--------------------------------------------------------------------------
Every injection emits a span event forge.defect.injected carrying the mode and
the timestamp, so the tampering is visible in SigNoz next to the run that
reacts to it. A judge watching the trace can see us break the app and see the
factory notice, in the same timeline. A demo that hides the injection is asking
to be disbelieved.

RESTORE IS A SNAPSHOT, NOT AN UNDO
--------------------------------------------------------------------------
Every file is copied byte-for-byte before it is touched, and restore puts the
copy back. Reversing an edit by re-editing is how you end up at 16:30 with an
app that is subtly different from the one you demoed. Injections stack; restore
undoes all of them.

Modes 1-3 refuse to touch anything outside pulse/ -- the same blast radius the
factory itself is held to.

OWNER: ROHIT.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from forge import config

log = logging.getLogger("forge.inject")

STATE_DIR = Path(os.getenv("FORGE_INJECT_DIR", ".forge_inject"))
STATE_FILE = STATE_DIR / "state.json"
SNAPSHOT_DIR = STATE_DIR / "snapshots"

#: The string mode 2 plants. Shaped to trip S10's sk- pattern, and obviously
#: a demo key to anyone who reads it.
DEMO_KEY = "sk-forgedemo0000ONLYADEMOKEY0000notreal"

SECURITY_HEADER_MARKERS = (
    "content-security-policy",
    "x-frame-options",
    "strict-transport-security",
    "x-content-type-options",
    "referrer-policy",
    "securityheaders",
    "security_headers",
    "forge:security-headers",
)


class InjectionUnavailable(RuntimeError):
    """The defect could not be injected -- said out loud rather than pretended."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _pulse_port() -> int:
    parsed = urlparse(config.PULSE_BASE_URL)
    return parsed.port or (443 if parsed.scheme == "https" else 80)


# --------------------------------------------------------------------------
# state
# --------------------------------------------------------------------------
def _load_state() -> dict:
    if not STATE_FILE.exists():
        return {"injections": []}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"injections": []}


def _save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _snapshot(path: Path) -> str:
    """Copy a file byte-for-byte before touching it. Returns the copy's path."""
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    flat = str(path).replace("\\", "/").replace("/", "__")
    target = SNAPSHOT_DIR / flat
    if not target.exists():  # first injection wins -- it holds the pristine copy
        shutil.copy2(path, target)
    return str(target)


def _guard(path: Path) -> Path:
    """Injection is held to the same blast radius as the factory."""
    resolved = str(path).replace("\\", "/")
    if not resolved.startswith("pulse/"):
        raise InjectionUnavailable(f"refusing to modify {path} -- injection only touches pulse/")
    if not path.exists():
        raise InjectionUnavailable(
            f"{path} does not exist. The Pulse scaffold has not landed yet, so there is nothing "
            "to inject a defect into."
        )
    return path


def _record(mode: int, detail: dict) -> None:
    """Write it down, and put it on the trace."""
    from forge import telemetry

    state = _load_state()
    entry = {"mode": mode, "at": _now(), **detail}
    state["injections"].append(entry)
    _save_state(state)

    with telemetry.stage_span("forge.inject", f"inject-{mode}") as span:
        if span is not None:
            span.set_attribute("defect.mode", mode)
            span.set_attribute("defect.at", entry["at"])
            for key, value in detail.items():
                span.set_attribute(f"defect.{key}", str(value)[:300])
            span.add_event("forge.defect.injected", {"mode": mode, "timestamp": entry["at"]})
    log.warning("DEFECT INJECTED: mode %s at %s -- %s", mode, entry["at"], detail)


# --------------------------------------------------------------------------
# mode 1 -- take the security headers back out
# --------------------------------------------------------------------------
def _main_py() -> Path:
    return _guard(Path(config.PULSE_DIR) / "main.py")


def mode_1() -> dict:
    """Remove the security-headers middleware the factory added.

    Only meaningful once a fix run has put headers in -- which is the demo
    order: the factory closes S1-S5, a judge takes the middleware out, the next
    audit catches it again. If nothing is there to remove, say so rather than
    reporting a defect that was not injected.
    """
    path = _main_py()
    original = path.read_text(encoding="utf-8")
    lines = original.splitlines(keepends=True)

    kept, removed = [], []
    skipping = False
    for line in lines:
        low = line.lower()
        if "forge:security-headers:end" in low:
            skipping = False
            removed.append(line)
            continue
        if skipping:
            removed.append(line)
            continue
        if "forge:security-headers" in low:
            skipping = True
            removed.append(line)
            continue
        if any(marker in low for marker in SECURITY_HEADER_MARKERS):
            removed.append(line)
            continue
        kept.append(line)

    if not removed:
        raise InjectionUnavailable(
            "No security-headers middleware was found in pulse/main.py, so there is nothing to "
            "remove. Run a fix that adds the headers first -- mode 1 undoes the factory's own work."
        )

    snapshot = _snapshot(path)
    path.write_text("".join(kept), encoding="utf-8")
    return {"file": str(path), "lines_removed": len(removed), "snapshot": snapshot}


# --------------------------------------------------------------------------
# mode 2 -- leave a key in a template comment
# --------------------------------------------------------------------------
def _a_template() -> Path:
    directory = Path(config.PULSE_DIR) / "templates"
    preferred = directory / "base.html"
    if preferred.exists():
        return _guard(preferred)
    candidates = sorted(directory.glob("*.html")) if directory.is_dir() else []
    if not candidates:
        raise InjectionUnavailable(
            f"No templates found under {directory}. The Pulse scaffold has not landed yet."
        )
    return _guard(candidates[0])


def mode_2() -> dict:
    """The comment every model leaves behind at least once."""
    path = _a_template()
    snapshot = _snapshot(path)
    comment = f"\n<!-- TODO: move this to env before launch  api_key = {DEMO_KEY} -->\n"
    path.write_text(path.read_text(encoding="utf-8") + comment, encoding="utf-8")
    return {"file": str(path), "planted": DEMO_KEY[:6] + "...", "snapshot": snapshot}


# --------------------------------------------------------------------------
# mode 3 -- open the docs, add an unguarded admin route
# --------------------------------------------------------------------------
ADMIN_ROUTE = '''

# --- injected by forge.inject mode 3 -----------------------------------
@app.get("/admin")
def forge_injected_admin():
    """No auth, on purpose. Injected defect -- remove with make restore."""
    return {"actions": ["rebuild_index", "flush_cache", "rotate_keys"]}
# -----------------------------------------------------------------------
'''


def mode_3() -> dict:
    path = _main_py()
    original = path.read_text(encoding="utf-8")
    snapshot = _snapshot(path)

    patched, opened = original, False
    for guarded in ("docs_url=None,", "docs_url=None", 'docs_url="/docs"'):
        if guarded in patched:
            patched = patched.replace(guarded, "", 1)
            opened = True
            break

    added_admin = "forge_injected_admin" not in patched
    if added_admin:
        patched = patched + ADMIN_ROUTE

    if patched == original:
        raise InjectionUnavailable(
            "The docs endpoint is already open and an injected /admin route is already present -- "
            "mode 3 has nothing left to do."
        )

    path.write_text(patched, encoding="utf-8")
    return {"file": str(path), "docs_unguarded": opened, "admin_added": added_admin, "snapshot": snapshot}


# --------------------------------------------------------------------------
# mode 4 -- stop Pulse. The most important one.
# --------------------------------------------------------------------------
def _pids_on_port(port: int) -> list[int]:
    try:
        import psutil
    except ImportError:
        return _pids_on_port_fallback(port)

    pids = set()
    try:
        for conn in psutil.net_connections(kind="inet"):
            if conn.laddr and conn.laddr.port == port and conn.pid:
                if conn.status in (psutil.CONN_LISTEN, psutil.CONN_ESTABLISHED):
                    pids.add(conn.pid)
    except Exception as exc:  # permissions vary by platform
        log.warning("psutil could not enumerate connections (%s), falling back", exc)
        return _pids_on_port_fallback(port)
    return sorted(pids)


def _pids_on_port_fallback(port: int) -> list[int]:
    """netstat on Windows, lsof elsewhere. Used only if psutil is unavailable."""
    try:
        if os.name == "nt":
            out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=10).stdout
            pids = set()
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 5 and parts[1].endswith(f":{port}") and parts[3] == "LISTENING":
                    pids.add(int(parts[4]))
            return sorted(pids)
        out = subprocess.run(["lsof", "-ti", f":{port}"], capture_output=True, text=True, timeout=10).stdout
        return sorted({int(p) for p in out.split() if p.strip().isdigit()})
    except Exception as exc:
        log.error("could not determine what is listening on port %s: %s", port, exc)
        return []


def _port_open(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def mode_4(port: int | None = None, wait: float = 10.0) -> dict:
    """Stop the Pulse process entirely.

    Records each process's command line and working directory BEFORE killing it,
    so restore brings back exactly what was running rather than a guess at it.
    Children are taken first -- uvicorn --reload runs a supervisor and a worker,
    and killing only the parent leaves the port held.
    """
    port = port or _pulse_port()
    if not _port_open(port):
        raise InjectionUnavailable(
            f"Nothing is listening on port {port}, so Pulse is already down. Mode 4 has nothing "
            "to stop -- the audit will already be seeing an outage."
        )

    pids = _pids_on_port(port)
    if not pids:
        raise InjectionUnavailable(
            f"Port {port} is serving, but the owning process could not be identified. Refusing to "
            "kill something at random."
        )

    stopped = []
    try:
        import psutil
    except ImportError:
        psutil = None

    for pid in pids:
        record = {"pid": pid}
        try:
            if psutil is not None:
                proc = psutil.Process(pid)
                record["cmdline"] = proc.cmdline()
                record["cwd"] = proc.cwd()
                for child in proc.children(recursive=True):
                    try:
                        child.kill()
                    except Exception:
                        pass
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except Exception:
                    proc.kill()
            else:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, timeout=10)
                else:
                    os.kill(pid, 15)
            stopped.append(record)
        except Exception as exc:
            log.error("could not stop pid %s: %s", pid, exc)

    deadline = time.time() + wait
    while time.time() < deadline and _port_open(port):
        time.sleep(0.2)

    if _port_open(port):
        raise InjectionUnavailable(
            f"Port {port} is still serving after stopping {[p['pid'] for p in stopped]}. Pulse was "
            "not stopped, so the demo would be showing something that did not happen."
        )

    return {"port": port, "stopped": stopped, "confirmed_down": True}


def _restart(record: dict, port: int, wait: float = 20.0) -> bool:
    cmdline = record.get("cmdline")
    cwd = record.get("cwd") or os.getcwd()
    if not cmdline:
        override = os.getenv("FORGE_PULSE_START_CMD")
        cmdline = (
            override.split()
            if override
            else [sys.executable, "-m", "uvicorn", os.getenv("PULSE_APP", "pulse.main:app"),
                  "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"]
        )
    log.info("restarting Pulse: %s (cwd=%s)", " ".join(cmdline), cwd)
    try:
        subprocess.Popen(cmdline, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as exc:
        log.error("could not restart Pulse: %s", exc)
        return False
    deadline = time.time() + wait
    while time.time() < deadline:
        if _port_open(port):
            return True
        time.sleep(0.2)
    return False


# --------------------------------------------------------------------------
# the public surface
# --------------------------------------------------------------------------
MODES = {
    1: ("Remove the security-headers middleware from pulse/main.py", mode_1),
    2: ("Add an API-key-shaped string into a template comment", mode_2),
    3: ("Un-guard the /docs route and add an /admin route with no auth", mode_3),
    4: ("Stop the Pulse process entirely (must classify as UPSTREAM_OUTAGE)", mode_4),
}


def inject(mode: int) -> dict:
    """Inject one defect. Raises InjectionUnavailable rather than pretending."""
    mode = int(mode)
    if mode not in MODES:
        raise InjectionUnavailable(f"unknown mode {mode}; choose one of {sorted(MODES)}")
    description, run = MODES[mode]
    detail = run()
    detail["description"] = description
    _record(mode, detail)
    return detail


def restore() -> dict:
    """Put everything back: files from snapshots, Pulse from its own command line."""
    state = _load_state()
    injections = state.get("injections", [])
    restored_files, restarted, problems = [], False, []

    for entry in reversed(injections):
        snapshot = entry.get("snapshot")
        target = entry.get("file")
        if snapshot and target and Path(snapshot).exists():
            try:
                shutil.copy2(snapshot, target)
                restored_files.append(target)
            except Exception as exc:
                problems.append(f"could not restore {target}: {exc}")

        if entry.get("mode") == 4 and not restarted:
            port = entry.get("port") or _pulse_port()
            if _port_open(port):
                restarted = True  # something is already serving again
                continue
            records = entry.get("stopped") or [{}]
            restarted = any(_restart(record, port) for record in records)
            if not restarted:
                problems.append(
                    f"Pulse did not come back on port {port}. Start it by hand -- "
                    "'make up', or uvicorn directly."
                )

    if SNAPSHOT_DIR.exists():
        shutil.rmtree(SNAPSHOT_DIR, ignore_errors=True)
    _save_state({"injections": []})

    result = {
        "files_restored": sorted(set(restored_files)),
        "pulse_restarted": restarted,
        "problems": problems,
        "injections_undone": len(injections),
    }
    log.info("restore: %s", result)
    return result


def status() -> dict:
    state = _load_state()
    port = _pulse_port()
    return {
        "active_injections": state.get("injections", []),
        "pulse_port": port,
        "pulse_serving": _port_open(port),
    }


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Inject a defect a judge can choose, or put everything back.",
        epilog="modes: " + " | ".join(f"{k} {v[0]}" for k, v in MODES.items()),
    )
    parser.add_argument("mode", nargs="?", help="1, 2, 3 or 4")
    parser.add_argument("--restore", action="store_true", help="undo every injection")
    parser.add_argument("--status", action="store_true", help="what is currently injected")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    try:
        if args.status:
            state = status()
            print(f"\n  Pulse on port {state['pulse_port']}: "
                  f"{'serving' if state['pulse_serving'] else 'DOWN'}")
            if not state["active_injections"]:
                print("  no active injections\n")
            for entry in state["active_injections"]:
                print(f"  mode {entry['mode']} injected at {entry['at']} -- {entry.get('description', '')}")
            print()
            return 0

        if args.restore:
            result = restore()
            print(f"\n  undid {result['injections_undone']} injection(s)")
            for path in result["files_restored"]:
                print(f"  restored {path}")
            if result["pulse_restarted"]:
                print("  Pulse is serving again")
            for problem in result["problems"]:
                print(f"  PROBLEM: {problem}")
            print()
            return 1 if result["problems"] else 0

        if not args.mode:
            parser.print_help()
            return 1

        detail = inject(args.mode)
        print(f"\n  INJECTED mode {args.mode}: {detail['description']}")
        for key, value in detail.items():
            if key != "description":
                print(f"    {key}: {value}")
        print("\n  The next audit will see this. 'python -m forge.inject --restore' puts it back.\n")
        return 0

    except InjectionUnavailable as exc:
        print(f"\n  CANNOT INJECT: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

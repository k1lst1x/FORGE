"""
forge/config.py -- environment, loaded once, from .env.

The .env file existed for hours with real Port, SigNoz and Bright Data
credentials in it and nothing ever read it, so every integration silently fell
back to a stub. Loading it is the first thing that happens on import of
anything in forge/.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger("forge.config")

#: Where the backend package lives. .env and the state directory hang off this.
REPO_ROOT = Path(__file__).resolve().parent.parent


def _find_project_root(start: Path) -> Path:
    """The directory that actually holds watchers/, policy/ and contracts/.

    The migration moved the code into backend/app/ but left watchers/, policy/,
    contracts/ and the scrape output at the REPO ROOT, one level above
    REPO_ROOT -- which was never updated to match. brightdata.watcher() went
    looking for backend/watchers/books.yaml, did not find it, and returned {}.
    The scraper was configured out of existence: no target_url, no contract, no
    output path, and the only evidence was a single log line. Nothing crashed,
    which is exactly why it went unnoticed.

    Walk up and take the first directory with that layout. Falls back to
    `start`, so if backend/ ever owns these directories this keeps working with
    no further change.
    """
    for candidate in (start, *start.parents):
        if (candidate / "watchers").is_dir() and (candidate / "policy").is_dir():
            return candidate
    return start


#: Where the factory's DATA and CONFIG live: watchers/, policy/, contracts/,
#: data/. Not the same as REPO_ROOT while the forge/ migration is unfinished.
PROJECT_ROOT = _find_project_root(REPO_ROOT)


def project_path(value: str | Path) -> Path:
    """Resolve a config-relative path against PROJECT_ROOT, absolutes intact."""
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _load_env() -> None:
    """Read .env into the process environment. Real values win over blanks."""
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path, override=False)
    except ImportError:  # no dependency required for something this simple
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())

    # An empty value is not a credential. Drop the blanks so every
    # `if os.getenv(...)` check in the codebase means what it says.
    for key in list(os.environ):
        if os.environ.get(key) == "":
            del os.environ[key]


_load_env()


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


PULSE_BASE_URL = os.getenv("PULSE_BASE_URL", "http://localhost:8100")
PULSE_DIR = os.getenv("PULSE_DIR", "pulse")
TESTS_DIR = os.getenv("TESTS_DIR", "tests")
# Resolved against PROJECT_ROOT: policy/ is at the repo root, not under
# backend/, so a bare relative path only worked when cwd happened to be right.
POLICY_PATH = str(project_path(os.getenv("FORGE_POLICY_PATH", "policy/audit_policy.yaml")))

FORGE_CONTROL_PORT = _int("FORGE_CONTROL_PORT", 8000)
FORGE_CONTROL_URL = os.getenv("FORGE_CONTROL_URL", f"http://localhost:{FORGE_CONTROL_PORT}")

AUDIT_INTERVAL_SECONDS = _int("AUDIT_INTERVAL_SECONDS", 300)

#: The scrape runs on its OWN clock, deliberately slower than the audit. Bright
#: Data falls back to a batch job on this target and a batch run takes minutes,
#: so starting one every audit tick queued work faster than it could finish. The
#: two are decoupled: the audit reads whatever is in data/books.json regardless
#: of when it was written, so a slow scrape delays nothing.
SCRAPE_INTERVAL_SECONDS = _int("SCRAPE_INTERVAL_SECONDS", 900)
AUDIT_ROUTES = [r.strip() for r in os.getenv("AUDIT_ROUTES", "/,/products,/security").split(",") if r.strip()]
MAX_PLAN_ATTEMPTS = _int("FORGE_MAX_PLAN_ATTEMPTS", 3)
FORGE_RUN_TIMEOUT_SECONDS = float(os.getenv("FORGE_RUN_TIMEOUT_SECONDS", "900"))
RELEASE_SETTLE_SECONDS = float(os.getenv("FORGE_RELEASE_SETTLE_SECONDS", "0"))
PORT_GATE_MODE = os.getenv("PORT_GATE_MODE", "poll")
ENGINE_RAISES = os.getenv("FORGE_ENGINE_RAISE", "0") in ("1", "true", "True")

STATE_DIR = Path(os.getenv("FORGE_STATE_DIR", str(REPO_ROOT / ".forge_state")))

PORT_CLIENT_ID = os.getenv("PORT_CLIENT_ID")
PORT_CLIENT_SECRET = os.getenv("PORT_CLIENT_SECRET")
PORT_API_BASE = os.getenv("PORT_API_BASE")  # auto-detected when unset
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO")  # owner/name, auto-detected from git remote
BRIGHTDATA_API_TOKEN = os.getenv("BRIGHTDATA_API_TOKEN")
SIGNOZ_INGESTION_KEY = os.getenv("SIGNOZ_INGESTION_KEY")
SIGNOZ_REGION = os.getenv("SIGNOZ_REGION", "us")


def missing() -> dict[str, bool]:
    """What is genuinely not configured. Used by /api/status and at startup."""
    return {
        "llm": not (
            os.getenv("ANTHROPIC_API_KEY")
            or os.getenv("FORGE_ANTHROPIC_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or os.getenv("FORGE_OPENAI_API_KEY")
        ),
        "github": not GITHUB_TOKEN,
        "port": not (PORT_CLIENT_ID and PORT_CLIENT_SECRET),
        "brightdata": not BRIGHTDATA_API_TOKEN,
        "signoz": not SIGNOZ_INGESTION_KEY,
    }

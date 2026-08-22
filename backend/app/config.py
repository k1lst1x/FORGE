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

REPO_ROOT = Path(__file__).resolve().parent.parent


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
POLICY_PATH = os.getenv("FORGE_POLICY_PATH", "policy/audit_policy.yaml")

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

"""forge/config.py — environment.  OWNER: DAMIR (Block 1).

STUB: reads env with defaults. Damir's real one fails loudly at startup naming
the missing variable. Only the names below are engine-visible.
"""
from __future__ import annotations

import os


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


PULSE_BASE_URL = os.getenv("PULSE_BASE_URL", "http://localhost:8100")
PULSE_DIR = os.getenv("PULSE_DIR", "pulse")
TESTS_DIR = os.getenv("TESTS_DIR", "tests")
POLICY_PATH = os.getenv("FORGE_POLICY_PATH", "policy/audit_policy.yaml")

AUDIT_INTERVAL_SECONDS = _int("AUDIT_INTERVAL_SECONDS", 300)
MAX_PLAN_ATTEMPTS = _int("FORGE_MAX_PLAN_ATTEMPTS", 3)  # 1 attempt + 2 retries
RELEASE_SETTLE_SECONDS = float(os.getenv("FORGE_RELEASE_SETTLE_SECONDS", "0"))
PORT_GATE_MODE = os.getenv("PORT_GATE_MODE", "poll")
ENGINE_RAISES = os.getenv("FORGE_ENGINE_RAISE", "0") in ("1", "true", "True")

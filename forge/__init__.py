"""Compatibility shim: Pulse still imports forge.*, which now lives in backend/app."""

from __future__ import annotations

import sys
from importlib import import_module
from pathlib import Path

_backend = Path(__file__).resolve().parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))


def __getattr__(name: str):
    return import_module(f"app.{name}")

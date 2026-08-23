"""Compatibility shim: Pulse and tests/ still import forge.*, which now lives
in backend/app.

`__getattr__` alone is not enough. It covers attribute access --

    from forge import llm          # -> forge.__getattr__("llm")

-- but NOT a submodule import:

    from forge.llm import BudgetExceeded
    import forge.models

Those go through the import system, which never consults a package's
`__getattr__`; it looks for a real forge/llm.py, does not find one, and raises
ModuleNotFoundError. That is why ten of the modules under tests/ still failed
to collect after the shim landed.

A meta path finder covers both, and covers them LAZILY -- eagerly aliasing
every module would import the whole factory (and its side effects) the moment
anything touches `forge`.

Identity matters here: the finder resolves forge.X to the SAME module object as
app.X rather than loading the file a second time. Two copies of forge.llm would
mean two separate _SPEND counters and two budget ceilings, so a test patching
one would silently not affect the code using the other.

Real modules under forge/ (status.py, and the console/ directory) still win, so
nothing already working is redirected.
"""

from __future__ import annotations

import importlib.abc
import importlib.machinery
import importlib.util
import sys
from importlib import import_module
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent / "backend"

if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

_PREFIX = __name__ + "."


class _AliasLoader(importlib.abc.Loader):
    """Loads forge.X by handing back the already-imported app.X."""

    def __init__(self, target: str) -> None:
        self._target = target

    def create_module(self, spec: importlib.machinery.ModuleSpec):
        return import_module(self._target)

    def exec_module(self, module) -> None:
        """No-op: app.X executed when it was imported."""


class _ForgeAliasFinder(importlib.abc.MetaPathFinder):
    """Maps forge.<name> onto app.<name>, unless forge/<name>.py really exists."""

    def find_spec(self, fullname: str, path=None, target=None):
        if not fullname.startswith(_PREFIX):
            return None

        sub = fullname[len(_PREFIX):]
        # A real module or package under forge/ takes precedence.
        if (_HERE / f"{sub}.py").exists() or (_HERE / sub).is_dir():
            return None

        target_name = f"app.{sub}"
        try:
            if importlib.util.find_spec(target_name) is None:
                return None
        except (ImportError, AttributeError, ValueError):
            return None

        return importlib.machinery.ModuleSpec(fullname, _AliasLoader(target_name))


if not any(isinstance(finder, _ForgeAliasFinder) for finder in sys.meta_path):
    sys.meta_path.insert(0, _ForgeAliasFinder())


def __getattr__(name: str):
    return import_module(f"app.{name}")

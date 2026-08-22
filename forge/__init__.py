"""FORGE -- a factory that builds software, then finds the flaws in what it built.

Importing anything under forge/ loads .env first. This is not a convenience:
forge.llm reads its provider and model from the environment at import time, and
it does not import forge.config, so importing llm directly used to skip .env
entirely and silently fall back to the wrong provider with no credentials.
"""

from forge import config as config  # noqa: F401  -- side effect: loads .env

__version__ = "3.0.0"

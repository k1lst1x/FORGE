"""FORGE -- a factory that builds software, then finds the flaws in what it built.

Importing anything under forge/ loads .env first. This is not a convenience:
forge.llm reads its provider and model from the environment at import time, and
it does not import app.config, so importing llm directly used to skip .env
entirely and silently fall back to the wrong provider with no credentials.

Two loaders, and BOTH are needed. app.core.config is pydantic BaseSettings: it
reads .env into a settings object, but it does NOT put anything into
os.environ. app.config._load_env() is the one that does. Everything migrated
out of the old forge/ package -- llm, triage, planner, telemetry, brightdata,
vcs -- still reads os.getenv, much of it at import time (triage.py and
planner.py bind MODEL at module level). With only the pydantic loader,
FORGE_LLM_PROVIDER and OPENAI_API_KEY were invisible to all of them and the
factory fell back to anthropic with no key -- exactly the failure this
docstring was written about, reintroduced by the move to app.core.config.

app.config is imported FIRST so os.environ is populated before pydantic reads
it. _load_env() uses override=False, so a real environment variable still wins
over the file.
"""

from app import config  # noqa: F401  -- side effect: .env -> os.environ
from app.core.config import settings  # noqa: F401  -- side effect: .env -> Settings

__version__ = "3.0.0"

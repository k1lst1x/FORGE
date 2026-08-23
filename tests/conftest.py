"""
tests/conftest.py -- the suite must not depend on .env.

Once .env started loading for real, the tests inherited whatever provider,
model and budget happened to be configured: switching FORGE_LLM_PROVIDER to
openai broke fifteen planner tests whose fake client is Anthropic-shaped, and
a real key in the environment would have made them spend money.

Every test runs against a pinned, offline configuration unless it explicitly
sets otherwise.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def hermetic_environment(monkeypatch, tmp_path):
    # The in-repo fakes speak the Anthropic wire shape. Tests that exercise the
    # OpenAI backend set this themselves, and their setenv wins over this one.
    monkeypatch.setenv("FORGE_LLM_PROVIDER", "anthropic")

    # Clear EVERY name llm looks under, not just the bare two. llm reads both
    # OPENAI_API_KEY and FORGE_OPENAI_API_KEY (same for Anthropic), so deleting
    # only the unprefixed pair left FORGE_OPENAI_API_KEY standing. provider()
    # then found credentials for openai, fell back to it, and drove the
    # Anthropic-shaped fakes down the OpenAI path:
    #
    #     'FakeAnthropic' object has no attribute 'chat'
    #
    # -- 68 failures that appeared only on a machine with the prefixed keys set,
    # which is exactly the leak this fixture exists to prevent. Derived from
    # llm._api_key_names so a new alias cannot reopen the hole.
    from forge import llm as _llm

    for _provider in ("anthropic", "openai"):
        for _name in _llm._api_key_names(_provider):
            monkeypatch.delenv(_name, raising=False)
    monkeypatch.setenv("FORGE_BUDGET_USD", "1000")
    monkeypatch.setenv("FORGE_TRACE_CONSOLE", "0")

    # Never write to the real state directory from a test run.
    state = tmp_path / "state"
    monkeypatch.setattr("forge.config.STATE_DIR", state, raising=False)
    for module, attr in (
        ("forge.store", "STATE_DIR"),
        ("forge.approvals", "QUEUE_FILE"),
    ):
        try:
            import importlib

            target = importlib.import_module(module)
            if attr == "STATE_DIR":
                monkeypatch.setattr(target, "STATE_DIR", state, raising=False)
                monkeypatch.setattr(target, "FINDINGS_FILE", state / "findings.json", raising=False)
                monkeypatch.setattr(target, "RUNS_FILE", state / "runs.json", raising=False)
                monkeypatch.setattr(target, "AUDIT_FILE", state / "last_audit.json", raising=False)
            else:
                monkeypatch.setattr(target, attr, state / "approvals.json", raising=False)
        except Exception:
            pass

    # Serving the app starts the scheduler, whose first tick now shells out to
    # the Bright Data CLI for up to 120s. A TestClient must never do that -- the
    # scrape has its own tests with the subprocess faked.
    monkeypatch.setattr(
        "forge.scheduler.scrape_once",
        lambda: {"ok": False, "rows": 0, "reason": "disabled in tests", "wrote": False},
        raising=False,
    )

    from forge import llm

    llm.reset_budget()
    yield
    llm.reset_budget()


@pytest.fixture(scope="session", autouse=True)
def never_autostart_the_audit_loop():
    """app.main's lifespan starts the audit loop; a test run must not.

    Session-scoped for the same reason as never_scrape_in_tests below:
    test_console_wiring builds its TestClient in a module-scoped fixture, which
    runs before any function-scoped patch could land.
    """
    try:
        from app.core.config import settings
    except Exception:
        yield
        return
    original = settings.audit_autostart
    settings.audit_autostart = False
    yield
    settings.audit_autostart = original


@pytest.fixture(scope="session", autouse=True)
def never_scrape_in_tests():
    """Disable the scheduler's scrape for the whole session.

    Session-scoped on purpose: module-scoped fixtures (test_console_wiring
    builds its TestClient in one) run BEFORE function-scoped ones, so a
    function-scoped patch arrives too late and the app's lifespan starts a
    real 120s Bright Data subprocess.
    """
    from forge import scheduler

    original = scheduler.scrape_once
    scheduler.scrape_once = lambda: {
        "ok": False, "rows": 0, "reason": "disabled in tests", "wrote": False,
    }
    yield
    scheduler.scrape_once = original

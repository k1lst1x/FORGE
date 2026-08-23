import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.auth import require_auth  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.factory.store import init_db  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def never_autostart_the_audit_loop():
    """The app now starts the audit loop in its lifespan.

    Session-scoped and applied by mutation rather than monkeypatch: a
    TestClient used as a context manager runs the lifespan, and module-scoped
    fixtures build theirs BEFORE any function-scoped fixture gets a chance to
    patch. A test suite must not spawn an autonomous loop that writes files and
    pushes entities to Port.
    """
    original = settings.audit_autostart
    settings.audit_autostart = False
    yield
    settings.audit_autostart = original


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "forge-test.db"))
    monkeypatch.setattr(settings, "port_client_id", "")
    monkeypatch.setattr(settings, "port_client_secret", "")
    monkeypatch.setattr(settings, "signoz_ingestion_key", "")
    monkeypatch.setattr(settings, "signoz_ingest_base_url", "")
    app.dependency_overrides[require_auth] = lambda: settings.auth_username
    init_db()
    yield
    app.dependency_overrides.clear()

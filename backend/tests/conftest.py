import pytest

from app.core.config import settings
from app.factory.store import init_db


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "forge-test.db"))
    init_db()

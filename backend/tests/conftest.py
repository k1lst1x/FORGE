import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import settings
from app.factory.store import init_db


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "forge-test.db"))
    monkeypatch.setattr(settings, "port_client_id", "")
    monkeypatch.setattr(settings, "port_client_secret", "")
    monkeypatch.setattr(settings, "signoz_ingestion_key", "")
    monkeypatch.setattr(settings, "signoz_ingest_base_url", "")
    init_db()

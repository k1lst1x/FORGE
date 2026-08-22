import pytest
from fastapi.testclient import TestClient
from backend.app.main import app

client = TestClient(app)

def test_docs_page_returns_200():
    response = client.get("/docs")
    assert response.status_code == 200

def test_openapi_json_returns_200():
    response = client.get("/openapi.json")
    assert response.status_code == 200
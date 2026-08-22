import pytest
from fastapi.testclient import TestClient
from backend.app.main import app
client = TestClient(app)

def test_home_page_returns_200():
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]

def test_products_page_returns_200():
    response = client.get("/products")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
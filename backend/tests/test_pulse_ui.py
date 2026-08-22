import pytest
from playwright.sync_api import Page, expect

BASE_URL = "http://127.0.0.1:8100"

def test_homepage_connection(page: Page):
    response = page.goto(BASE_URL)
    assert response is not None
    assert response.status in [200, 404]

def test_swagger_ui_loaded(page: Page):
    page.goto(f"{BASE_URL}/docs")
    expect(page).to_have_title("FastAPI - Swagger UI")
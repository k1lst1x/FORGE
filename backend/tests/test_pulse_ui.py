import pytest
from playwright.sync_api import Page, expect

BASE_URL = 'BASE_URL = "http://127.0.0.1:8100"'

def test_homepage_title_and_header(page: Page):
    page.goto(BASE_URL)
    expect(page).to_have_title("Pulse")
    expect(page.locator("h1")).to_be_visible()

def test_all_images_have_alt_attributes(page: Page):
    page.goto(BASE_URL)
    images = page.locator("img").all()
    for img in images:
        alt_text = img.get_attribute("alt")
        assert alt_text is not None and len(alt_text.strip()) > 0, (
            f"image {img.get_attribute('src')} does not contain the required alt attribute!"
        )
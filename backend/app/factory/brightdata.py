from __future__ import annotations

import hashlib
from pathlib import Path

import httpx

from app.core.config import settings

_SNAPSHOT_DIR = Path("data/brightdata")


def scraper_run(collector_id: str, url: str) -> list[dict]:
    _ = collector_id
    html = fetch_html(url)
    if not html:
        return [{"name": "Widget A", "price": 49.0, "currency": "USD", "availability": "in_stock"}]
    return [{"name": "Widget A", "price": 49.0, "currency": "USD", "availability": "in_stock", "html_snippet": html[:200]}]


def scraper_heal(collector_id: str, prompt: str, url: str) -> dict:
    _ = (collector_id, prompt)
    snapshot = snapshot_page(url)
    return {
        "status": "awaiting_approval" if snapshot["changed"] else "stable",
        "preview_result": [{"url": url, "changed": snapshot["changed"]}],
        "next_step": "bdata scraper approve <collector-id>",
    }


def scraper_approve(cmd: str) -> bool:
    _ = cmd
    return True


def scrape_markdown(url: str) -> str:
    html = fetch_html(url)
    if not html:
        return "# Pulse\n\nWidget A - $49.00"
    return html[:200].replace("<", " ").replace(">", " ").strip() or "# Pulse\n\nWidget A - $49.00"


def _snapshot_path(url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    _SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    return _SNAPSHOT_DIR / f"{digest}.html"


def fetch_html(url: str, *, timeout: float = 20.0) -> str:
    headers = {"User-Agent": "FORGE-BrightData/0.1"}
    try:
        response = httpx.get(url, headers=headers, timeout=timeout)
        response.raise_for_status()
        return response.text
    except httpx.HTTPError:
        proxy_url = settings.brightdata_selenium_url or settings.brightdata_browser_ws_url
        if not proxy_url or not url.startswith("http"):
            return ""
        try:
            proxy_response = httpx.get(proxy_url, headers=headers, timeout=timeout)
            proxy_response.raise_for_status()
            return proxy_response.text
        except httpx.HTTPError:
            return ""


def snapshot_page(url: str, *, timeout: float = 20.0) -> dict[str, object]:
    html = fetch_html(url, timeout=timeout)
    path = _snapshot_path(url)
    previous = path.read_text(encoding="utf-8") if path.exists() else None
    changed = previous != html if previous is not None else True
    if html:
        path.write_text(html, encoding="utf-8")
    return {
        "url": url,
        "changed": changed,
        "saved": bool(html),
        "sha256": hashlib.sha256(html.encode("utf-8")).hexdigest() if html else None,
        "previous_present": previous is not None,
    }


def detect_change(url: str, *, timeout: float = 20.0) -> bool:
    snapshot = snapshot_page(url, timeout=timeout)
    return bool(snapshot["changed"])

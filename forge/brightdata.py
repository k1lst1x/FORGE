"""forge/brightdata.py — Bright Data CLI wrapper.  OWNER: DAMIR.

STUB from the §08 stub session. Signatures FROZEN.
"""
from __future__ import annotations

STUB = True


def scraper_run(collector_id: str, url: str) -> list[dict]:
    return [{"name": "Widget A", "price": 49.0}]


def scraper_heal(collector_id: str, prompt: str, url: str) -> dict:
    return {
        "status": "awaiting_approval",
        "preview_result": [],
        "next_step": "bdata scraper approve ...",
    }


def scraper_approve(cmd: str) -> bool:
    return True


def scrape_markdown(url: str) -> str:
    return "# Pulse\n\nWidget A - $49.00"

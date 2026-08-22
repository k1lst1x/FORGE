"""
forge/brightdata.py -- real scraped data.

Two paths, same shape out:

  * a Bright Data collector when BRIGHTDATA_COLLECTOR_ID is set -- the CLI is
    driven as a subprocess from inside the workflow, never from a dashboard
  * a direct fetch-and-parse otherwise, so Pulse shows REAL product data now
    rather than a hardcoded list while a collector generates

The fallback target is books.toscrape.com, a site published expressly for
scraping practice. Real HTTP, real parsing, real freshness timestamps -- no
invented rows.

Results are cached to disk with the time they were fetched, so Pulse can show
an honest freshness badge and a scrape failure degrades to stale-with-a-label
rather than an empty page.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

from app import config

log = logging.getLogger("forge.brightdata")

STUB = False
COLLECTOR_ID = os.getenv("BRIGHTDATA_COLLECTOR_ID")
SOURCE_URL = os.getenv("SCRAPE_SOURCE_URL", "https://books.toscrape.com/")
CACHE_FILE = config.STATE_DIR / "scraped.json"
MAX_AGE_SECONDS = int(os.getenv("SCRAPE_MAX_AGE_SECONDS", "900"))

_AVAILABILITY = {"in stock": "in_stock", "out of stock": "out_of_stock"}


def _cache_read() -> dict | None:
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def _cache_write(rows: list[dict], source: str) -> None:
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(
        json.dumps({"rows": rows, "fetched_at": time.time(), "source": source}, indent=2),
        encoding="utf-8",
    )


def _parse_products(html: str, base_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict] = []
    for card in soup.select("article.product_pod"):
        title_el = card.select_one("h3 a")
        price_el = card.select_one(".price_color")
        stock_el = card.select_one(".instock, .availability")
        rating_el = card.select_one("p.star-rating")
        if not (title_el and price_el):
            continue
        raw_price = price_el.get_text(strip=True)
        try:
            price = float(raw_price.lstrip("£$€").strip())
        except ValueError:
            continue
        availability = (stock_el.get_text(strip=True).lower() if stock_el else "")
        rows.append({
            "name": title_el.get("title") or title_el.get_text(strip=True),
            "price": price,
            "currency": "GBP" if raw_price.startswith("£") else "USD",
            "availability": _AVAILABILITY.get(availability, "in_stock" if "in stock" in availability else "unknown"),
            "rating": (rating_el.get("class") or ["", ""])[-1].lower() if rating_el else None,
            "source": base_url,
        })
    return rows


def scraper_run(collector_id: str | None = None, url: str | None = None) -> list[dict]:
    """Structured rows from the live web. Cached, with the fetch time recorded."""
    url = url or SOURCE_URL
    collector_id = collector_id or COLLECTOR_ID

    cached = _cache_read()
    if cached and (time.time() - cached.get("fetched_at", 0)) < MAX_AGE_SECONDS:
        return cached["rows"]

    if collector_id:
        rows = _collector_run(collector_id, url)
        if rows:
            _cache_write(rows, f"brightdata:{collector_id}")
            return rows
        log.warning("collector %s returned nothing; falling back to a direct fetch", collector_id)

    try:
        response = httpx.get(url, timeout=20, follow_redirects=True,
                             headers={"User-Agent": "forge-pulse/3.0"})
        response.raise_for_status()
        rows = _parse_products(response.text, url)
        if rows:
            _cache_write(rows, url)
            log.info("scraped %s rows from %s", len(rows), url)
            return rows
        log.error("no products parsed from %s -- the page structure changed", url)
    except Exception as exc:
        log.error("scrape of %s failed: %s", url, exc)

    if cached:
        log.warning("serving stale scraped data from %s", time.ctime(cached.get("fetched_at", 0)))
        return cached["rows"]
    return []


def _collector_run(collector_id: str, url: str) -> list[dict]:
    """Drive the Bright Data CLI as a subprocess from inside the workflow."""
    command = ["npx", "-p", "@brightdata/cli", "bdata", "scraper", "run", collector_id, "--url", url, "--json"]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=180)
        if result.returncode != 0:
            log.error("bdata exited %s: %s", result.returncode, (result.stderr or "")[:300])
            return []
        return json.loads(result.stdout or "[]")
    except Exception as exc:
        log.error("bdata run failed: %s", exc)
        return []


def freshness() -> dict:
    cached = _cache_read() or {}
    fetched_at = cached.get("fetched_at")
    return {
        "fetched_at": fetched_at,
        "age_seconds": round(time.time() - fetched_at) if fetched_at else None,
        "source": cached.get("source"),
        "rows": len(cached.get("rows") or []),
    }


def scraper_heal(collector_id: str, prompt: str, url: str) -> dict:
    """Regenerate a collector whose selectors stopped matching, and stop for a
    human before committing -- we deliberately do not pass an auto-approve flag."""
    command = ["npx", "-p", "@brightdata/cli", "bdata", "scraper", "update", collector_id, prompt, "--url", url]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=600)
        return {
            "status": "awaiting_approval" if result.returncode == 0 else "failed",
            "preview_result": (result.stdout or "")[:2000],
            "next_step": f"bdata scraper approve {collector_id}",
        }
    except Exception as exc:
        return {"status": "failed", "preview_result": str(exc), "next_step": ""}


def scraper_approve(cmd: str) -> bool:
    try:
        return subprocess.run(cmd.split(), capture_output=True, text=True, timeout=120).returncode == 0
    except Exception:
        return False


def scrape_markdown(url: str) -> str:
    """The page as text, used as audit evidence."""
    try:
        response = httpx.get(url, timeout=15, follow_redirects=True,
                             headers={"User-Agent": "forge-audit/3.0"})
        soup = BeautifulSoup(response.text, "html.parser")
        return soup.get_text(" ", strip=True)[:4000]
    except Exception as exc:
        log.warning("could not fetch %s: %s", url, exc)
        return ""

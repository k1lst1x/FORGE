"""
pulse/routes/stock.py -- out-of-stock alerts from the scraped feed.

    GET /stock-alerts

Mount it from pulse/main.py in one line, the way security.py is:

    from pulse.routes import stock
    app.include_router(stock.router)

This file was two lines that referenced a `router` nobody had created, so
importing it raised NameError. It is not mounted, so nothing failed at runtime
-- it just sat there as a module that could not be loaded.

Reads the feed the scheduler last wrote; it never triggers a scrape. A page
load that shells out to the Bright Data CLI would queue a batch job per
refresh, which is the mistake pulse/main.py documents at length.
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


def _out_of_stock(rows: list[dict]) -> list[dict]:
    return [r for r in rows if "out" in str(r.get("availability", "")).lower()]


@router.get("/stock-alerts")
def stock_alerts() -> dict:
    """Which products the last successful scrape found out of stock.

    `age_seconds` is measured from last_success_at and is None when no scrape
    has ever succeeded. None must be rendered as "no data yet" -- never as 0,
    which would claim a feed that does not exist is perfectly fresh.
    """
    from forge import brightdata, store

    watcher = brightdata.watcher()
    data = store.read_scrape(watcher)
    if not data:
        return {
            "has_data": False,
            "alerts": [],
            "count": 0,
            "age_seconds": None,
            "source": watcher.get("target_url"),
        }

    alerts = _out_of_stock(data.get("rows") or [])
    return {
        "has_data": True,
        "alerts": alerts,
        "count": len(alerts),
        "age_seconds": store.scrape_age_seconds(watcher),
        "source": data.get("source") or watcher.get("target_url"),
        "last_success_at": data.get("last_success_at"),
    }

"""
pulse/main.py -- Pulse. The app the factory builds and audits.

Deliberately plain, per section 13: /docs open, an image with no alt text.
The factory finds these and fixes them.

Data is really scraped. Nothing here is hardcoded product data.
"""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from forge import brightdata  # noqa: E402
from pulse.routes import security  # noqa: E402

app = FastAPI(title="Pulse")  # note: /docs is open by default
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
app.include_router(security.router)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = "default-src 'self'; frame-ancestors 'none'"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    for header in ("Server", "X-Powered-By"):
        if header in response.headers:
            del response.headers[header]
    return response


def _feed() -> dict:
    """The product feed as the scheduler last wrote it.

    Rendering must never trigger a scrape: a page load would shell out to the
    Bright Data CLI and a browser refresh would queue another. The scheduler
    writes data/books.json; this only reads it.

    Returns a dict with `rows`, `age_seconds` and `source`. age_seconds is
    MEASURED from last_success_at and is None when no scrape has ever
    succeeded -- the template renders "no data yet" for that rather than
    substituting a number. Nothing here may invent a timestamp.
    """
    from forge import store

    watcher = brightdata.watcher()
    data = store.read_scrape(watcher)
    if not data:
        return {"rows": [], "age_seconds": None, "source": watcher.get("target_url"),
                "has_data": False, "last_success_at": None}
    return {
        "rows": data.get("rows") or [],
        "age_seconds": store.scrape_age_seconds(watcher),
        "source": data.get("source") or watcher.get("target_url"),
        "has_data": True,
        "last_success_at": data.get("last_success_at"),
        "contract_ok": data.get("contract_ok", True),
    }


def _products() -> list[dict]:
    return _feed()["rows"]


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    feed = _feed()
    rows = feed["rows"]
    return templates.TemplateResponse(
        request, "home.html",
        {"count": len(rows),
         "out_of_stock": len([r for r in rows
                              if "out" in str(r.get("availability", "")).lower()]),
         "feed": feed},
    )


@app.get("/products", response_class=HTMLResponse)
def products(request: Request):
    feed = _feed()
    rows = sorted(feed["rows"], key=lambda r: r.get("price") or 0, reverse=True)
    return templates.TemplateResponse(
        request, "products.html", {"rows": rows, "feed": feed},
    )


@app.get("/api/products")
def api_products():
    feed = _feed()
    return {"products": feed["rows"], "age_seconds": feed["age_seconds"],
            "source": feed["source"], "last_success_at": feed["last_success_at"],
            "has_data": feed["has_data"]}

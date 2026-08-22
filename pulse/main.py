"""
pulse/main.py -- Pulse. The app the factory builds and audits.

Deliberately plain and deliberately insecure, per section 13: no security
headers, /docs open, an image with no alt text. That is what a model writes
when you ask it for a quick FastAPI app -- which is the point the demo makes.
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


def _products() -> list[dict]:
    """Read what the last successful scrape wrote.

    Rendering must never trigger a scrape: a page load would shell out to the
    Bright Data CLI, and a browser refresh would queue another. `make scrape`
    writes data/books.json; this only reads it.
    """
    return brightdata.read_data().get("rows") or []


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    rows = _products()
    return templates.TemplateResponse(
        request, "home.html",
        {"count": len(rows),
         "out_of_stock": len([r for r in rows if r.get("availability") == "out_of_stock"]),
         "freshness": brightdata.freshness()},
    )


@app.get("/products", response_class=HTMLResponse)
def products(request: Request):
    rows = sorted(_products(), key=lambda r: r.get("price") or 0, reverse=True)
    return templates.TemplateResponse(
        request, "products.html", {"rows": rows, "freshness": brightdata.freshness()},
    )


@app.get("/api/products")
def api_products():
    return {"products": _products(), "freshness": brightdata.freshness()}

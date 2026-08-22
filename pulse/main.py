from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from pulse.routes import pricing, security

logger = logging.getLogger(__name__)
ENV = os.getenv("ENV", "production")
_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


class SecurityHeadersMiddleware:
    """Apply required security controls to every HTTP response."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if scope["path"] in {"/.env", "/.git/config", "/admin", "/debug"}:
            response = PlainTextResponse("Not Found", status_code=404)
            await response(scope, receive, send)
            return

        async def send_with_security_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() not in {b"server", b"x-powered-by"}
                ]
                headers.extend(
                    [
                        (
                            b"content-security-policy",
                            b"default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'self'",
                        ),
                        (b"x-frame-options", b"DENY"),
                        (b"strict-transport-security", b"max-age=31536000; includeSubDomains"),
                        (b"x-content-type-options", b"nosniff"),
                        (b"referrer-policy", b"no-referrer"),
                    ]
                )
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_security_headers)


app = FastAPI(
    docs_url="/docs" if ENV == "dev" else None,
    redoc_url="/redoc" if ENV == "dev" else None,
    openapi_url="/openapi.json" if ENV == "dev" else None,
)
app.add_middleware(SecurityHeadersMiddleware)
app.include_router(pricing.router)
app.include_router(security.router)


@app.exception_handler(Exception)
async def internal_error(request: Request, exc: Exception):
    logger.exception("Unhandled error while serving %s", request.url.path)
    return PlainTextResponse("Internal Server Error", status_code=500)


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return _TEMPLATES.TemplateResponse(request, "home.html", {"feed": {"has_data": False}})


@app.get("/products", response_class=HTMLResponse)
def products(request: Request):
    return _TEMPLATES.TemplateResponse(request, "products.html", {"feed": {"has_data": False}, "rows": []})

"""
tests/fixtures/insecure_app.py -- a stand-in for Pulse, for testing the audit.

This mirrors the scaffold spec in section 13: deliberately plain, deliberately
insecure. No security headers, /docs open, an image with no alt text. It is not
sabotage and it is not planted evidence -- it is what a model writes when you
ask it for a quick FastAPI app, which is the point the whole demo makes.

It exists so the audit engine can be validated before John's scaffold lands,
and it stays afterwards as a fixed target: if a check regresses, this catches
it without needing the real app running.

Runs on 8199, NOT 8100, so it never collides with the real Pulse.

    python tests/fixtures/insecure_app.py
"""
from __future__ import annotations

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

app = FastAPI()  # note: /docs is open by default

# what you get when you ask for "just let the frontend talk to it"
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

PAGE = """<!doctype html>
<html><head><title>Pulse</title>
<script src="https://cdn.example.com/chart.js"
        integrity="sha384-oqVuAfXRKap7fdgcCY5uykM6+R9GqQ8K/uxy9rx7HNQlGYl1kPzQho1wx4JwY8wC"
        crossorigin="anonymous"></script>
</head>
<body style="background:#0f172a;color:#f8fafc;font-family:system-ui;padding:2rem">
<h1>Pulse</h1>
<nav><a href="/">Home</a> - <a href="/products">Products</a> -
     <a href="/security">Security</a></nav>
<!-- TODO: move this before launch  api_key = sk-proj0aB1cD2eF3gH4iJ5kL6mN7oP8qR9sT0u -->
{body}
<p>Data from <a href="https://competitor.example.com">competitor.example.com</a></p>
<img src="/static/logo.png">
</body></html>
"""


@app.get("/", response_class=HTMLResponse)
def home(response: Response):
    response.set_cookie("session", "abc123")  # no Secure, no HttpOnly, no SameSite
    return PAGE.format(body="<p>Competitor pricing, refreshed hourly.</p>")


@app.get("/products", response_class=HTMLResponse)
def products():
    rows = "".join(f"<tr><td>Widget {n}</td><td>${n * 10}.00</td></tr>" for n in "ABC")
    return PAGE.format(body=f"<table>{rows}</table>")


@app.get("/admin", response_class=HTMLResponse)
def admin():
    return PAGE.format(body="<h2>Admin</h2><p>Rebuild index, flush cache.</p>")


@app.exception_handler(404)
async def debug_404(request: Request, exc):
    """A debug-mode not-found page. Flask and Django do exactly this."""
    body = (
        "<h1>404</h1><pre>Traceback (most recent call last):\n"
        '  File "/app/pulse/main.py", line 42, in dispatch\n'
        "    return await route.handler(request)\n"
        f"KeyError: {request.url.path!r}</pre>"
    )
    return HTMLResponse(body, status_code=404)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8199, log_level="warning")

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))

PLANS = [
    {
        "name": "Starter",
        "monthly_price": "$19",
        "yearly_price": "$190",
        "features": ["Track up to 25 products", "Daily price updates", "Email alerts"],
    },
    {
        "name": "Growth",
        "monthly_price": "$49",
        "yearly_price": "$490",
        "features": ["Track up to 250 products", "Hourly price updates", "Email and Slack alerts", "CSV exports"],
        "featured": True,
    },
    {
        "name": "Scale",
        "monthly_price": "$99",
        "yearly_price": "$990",
        "features": ["Track unlimited products", "Hourly price updates", "Priority support", "API access"],
    },
]

COMPARISON = [
    ("Products tracked", "25", "250", "Unlimited"),
    ("Price update frequency", "Daily", "Hourly", "Hourly"),
    ("Alerts", "Email", "Email and Slack", "Email and Slack"),
    ("CSV exports", "—", "Included", "Included"),
    ("API access", "—", "—", "Included"),
]


@router.get("/pricing", response_class=HTMLResponse)
def pricing(request: Request):
    return _TEMPLATES.TemplateResponse(
        request,
        "pricing.html",
        {"plans": PLANS, "comparison": COMPARISON},
    )

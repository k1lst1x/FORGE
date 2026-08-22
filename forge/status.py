"""
forge/status.py -- the status endpoint.

api.py is Damir's file and does not exist yet, so this ships as a router he
mounts in one line rather than as a second FastAPI app competing with his:

    from forge.status import router
    app.include_router(router)

Kept separate on purpose -- two people creating api.py is how the 11:00 merge
goes wrong.
"""
from __future__ import annotations

from fastapi import APIRouter

from forge import llm

router = APIRouter()


@router.get("/api/status")
def status() -> dict:
    """What the factory has spent, and on what."""
    return llm.budget_status()

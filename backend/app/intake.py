"""
forge/intake.py -- the front door.

    POST /intake/brief   {"title": "...", "description": "..."}

api.py is Damir's and does not exist yet, so this ships as a router he mounts
in one line rather than as a second app competing with his:

    from app.intake import router
    app.include_router(router)

It follows the contract he specified: return immediately and process in a
background task. A sender that waits on a full factory run -- plan, write,
test, audit, human approval -- will time out and retry, and we will get
duplicate runs for one brief.

THE POINT OF THIS ENDPOINT
--------------------------------------------------------------------------
It is the only thing that distinguishes Loop A from Loop B. A brief arrives
here, a finding arrives from the scheduler or a SigNoz alert, and both become a
ChangeRequest that enters the same eight steps. The factory does not know which
door its work came through.

OWNER: ROHIT (the intake shape). Damir owns auth on it.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel, Field

from app.models import new_run_id

log = logging.getLogger("forge.intake")

router = APIRouter()


class Brief(BaseModel):
    """What a person submits in Port."""

    description: str = Field(..., min_length=1, description="what the factory should build")
    title: str | None = Field(None, description="optional short name for the run")


class Accepted(BaseModel):
    accepted: bool
    run_id: str
    intake: str
    message: str


def _run_brief(description: str, title: str | None, run_id: str) -> None:
    """The background half. Imported late so the web layer starts fast."""
    from app import engine

    try:
        engine.run_from_brief(description, title=title, run_id=run_id)
    except Exception:  # a failed run must not take the service with it
        log.exception("brief run %s failed", run_id)


@router.post("/intake/brief", response_model=Accepted)
async def intake_brief(brief: Brief, background: BackgroundTasks) -> Accepted:
    """Accept a brief and start a factory run.

    Returns before the run does. The run_id is the handle for everything after:
    it is the Port entity, it is on every span, and it is how you find the trace.
    """
    run_id = new_run_id()
    log.info("brief accepted as %s: %s", run_id, (brief.title or brief.description)[:80])
    background.add_task(_run_brief, brief.description, brief.title, run_id)
    return Accepted(
        accepted=True,
        run_id=run_id,
        intake="brief",
        message="Brief accepted. It enters the same eight steps a finding does; "
        "watch the run in Port or follow its trace.",
    )

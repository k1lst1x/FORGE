from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.factory import router as factory_router
from app.api.auth import router as auth_router
from app.api.console import router as console_router
from app.core.config import settings
from app.factory import portal, scheduler, vcs
from app.factory.integrations import smoke_checks
from app.factory.store import init_db


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    init_db()
    vcs.ensure_gh_available()
    if settings.audit_autostart:
        # Without this the scheduler sat at "down" and /api/status had no next
        # audit to report, so the console header showed a countdown that never
        # counted. An audit loop that has to be started by hand is not a
        # factory that audits every five minutes.
        scheduler.start()
    try:
        yield
    finally:
        await scheduler.stop()


app = FastAPI(
    title=settings.project_name,
    summary="API surface for the FORGE agentic software factory.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["system"])
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "project": settings.project_name,
        "event": settings.event_name,
        "port": portal.port_health(),
    }


@app.get("/health/integrations", tags=["system"])
def integrations_health() -> dict[str, object]:
    return smoke_checks()


app.include_router(factory_router)
app.include_router(auth_router)

# The operator console speaks forge-control's /api/* vocabulary, which nothing
# here serves. Bridged in app/api/console.py -- delete that file and this line
# once the real forge-control is reachable again.
app.include_router(console_router)

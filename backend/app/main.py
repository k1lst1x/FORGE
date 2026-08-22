from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.factory import router as factory_router
from app.core.config import settings
from app.factory import portal
from app.factory.integrations import smoke_checks
from app.factory.store import init_db


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    init_db()
    yield


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

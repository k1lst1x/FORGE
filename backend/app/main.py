from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.factory import router as factory_router
from app.core.config import settings

app = FastAPI(
    title=settings.project_name,
    summary="API surface for the FORGE agentic software factory.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "project": settings.project_name,
        "event": settings.event_name,
    }


app.include_router(factory_router)

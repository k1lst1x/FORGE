import asyncio
from datetime import UTC, datetime

from app.core.config import settings
from app.factory import engine

_task: asyncio.Task | None = None
_last_run_id: str | None = None
_last_started_at: str | None = None
_last_error: str | None = None


def status() -> dict[str, object]:
    return {
        "running": _task is not None and not _task.done(),
        "interval_seconds": settings.audit_interval_seconds,
        "last_run_id": _last_run_id,
        "last_started_at": _last_started_at,
        "last_error": _last_error,
    }


def start() -> dict[str, object]:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_audit_loop())
    return status()


async def stop() -> dict[str, object]:
    global _task
    if _task is not None and not _task.done():
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
    _task = None
    return status()


async def _audit_loop() -> None:
    while True:
        await run_once()
        await asyncio.sleep(settings.audit_interval_seconds)


async def run_once() -> str:
    global _last_error, _last_run_id, _last_started_at
    _last_started_at = datetime.now(UTC).isoformat()
    try:
        cr = engine.run_from_brief(
            brief="Scheduled audit of registered Pulse routes.",
            trigger="scheduler",
        )
        _last_run_id = cr.run_id
        _last_error = None
        return cr.run_id
    except Exception as exc:
        _last_error = str(exc)
        raise

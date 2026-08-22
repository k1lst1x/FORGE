import asyncio
import threading
from datetime import UTC, datetime

from app.core.config import settings
from app.factory import engine

_task: asyncio.Task | None = None
_loop: asyncio.AbstractEventLoop | None = None
_loop_thread: threading.Thread | None = None
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
    global _task, _loop, _loop_thread
    if _task is not None and not _task.done():
        return status()

    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
        _loop_thread = threading.Thread(target=_loop.run_forever, daemon=True)
        _loop_thread.start()

    async def _schedule_task() -> None:
        global _task
        _task = asyncio.create_task(_audit_loop())

    future = asyncio.run_coroutine_threadsafe(_schedule_task(), _loop)
    future.result(timeout=5)
    return status()


async def stop() -> dict[str, object]:
    global _task, _loop, _loop_thread
    if _task is not None and _loop is not None:
        async def _cancel_task() -> None:
            if _task is not None and not _task.done():
                _task.cancel()
                try:
                    await _task
                except asyncio.CancelledError:
                    pass

        result = asyncio.run_coroutine_threadsafe(_cancel_task(), _loop)
        try:
            result.result(timeout=5)
        except Exception:
            pass
    _task = None
    if _loop is not None:
        _loop.call_soon_threadsafe(_loop.stop)
        if _loop_thread is not None:
            _loop_thread.join(timeout=5)
        _loop = None
        _loop_thread = None
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

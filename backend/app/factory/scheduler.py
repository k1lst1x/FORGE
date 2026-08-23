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
_failures = 0


def status() -> dict[str, object]:
    return {
        "running": _task is not None and not _task.done(),
        "interval_seconds": settings.audit_interval_seconds,
        "last_run_id": _last_run_id,
        "last_started_at": _last_started_at,
        "last_error": _last_error,
        "failures": _failures,
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
        # One bad tick must not kill the loop. Before this, a single failing
        # audit raised out of the task and the scheduler silently stopped
        # auditing for the rest of the process's life while status() still
        # reported it as running until the task object was inspected.
        try:
            await run_once()
        except Exception:  # noqa: BLE001 - recorded on _last_error by run_once
            pass
        await asyncio.sleep(settings.audit_interval_seconds)


def _run_once_blocking() -> str:
    global _last_error, _last_run_id, _failures
    try:
        cr = engine.run_from_brief(
            brief="Scheduled audit of registered Pulse routes.",
            trigger="scheduler",
        )
        _last_run_id = cr.run_id
        _last_error = None
        from app.factory.observability import publish_after_audit

        publish_after_audit()
        return cr.run_id
    except Exception as exc:
        _failures += 1
        _last_error = str(exc)
        raise


async def run_once() -> str:
    """One audit, off the event loop.

    The body is blocking -- the engine does sync httpx and subprocess work --
    and running it directly on the scheduler's loop starved that loop for the
    length of a run. That broke start(): create_task() queues the loop task
    ahead of the callback that resolves start()'s future, so the first tick ran
    first and start() timed out after 5s. It only surfaced once Port and SigNoz
    were configured and a run grew past 5 seconds; before that it fit inside
    the window by luck. The whole app then failed to boot:

        File "app/factory/scheduler.py", line 43, in start
            future.result(timeout=5)
        TimeoutError

    In a worker thread the loop stays free, so start() returns at once, stop()
    can cancel promptly, and status polls answer while a run is in flight.
    """
    global _last_started_at
    _last_started_at = datetime.now(UTC).isoformat()
    return await asyncio.to_thread(_run_once_blocking)

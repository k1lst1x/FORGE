from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from time import perf_counter, time_ns
from uuid import uuid4

import httpx

from app.core.config import settings


def new_trace_id() -> str:
    return uuid4().hex


def _sigNoz_enabled() -> bool:
    return bool(settings.signoz_ingestion_key and settings.signoz_ingest_base_url)


def _emit_sigNoz_span(name: str, run_id: str, trace_id: str | None, duration_ms: float) -> None:
    if not _sigNoz_enabled():
        return
    payload = {
        "resourceSpans": [
            {
                "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "forge"}}]},
                "scopeSpans": [
                    {
                        "scope": {"name": "forge-factory", "version": "0.1.0"},
                        "spans": [
                            {
                                "name": name,
                                "kind": 2,
                                "startTimeUnixNano": str(time_ns()),
                                "endTimeUnixNano": str(time_ns() + max(int(duration_ms * 1_000_000), 1)),
                                "attributes": [
                                    {"key": "run_id", "value": {"stringValue": run_id}},
                                    {"key": "trace_id", "value": {"stringValue": trace_id or ""}},
                                    {"key": "duration_ms", "value": {"doubleValue": float(duration_ms)}},
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
    }
    try:
        httpx.post(
            f"{settings.signoz_ingest_base_url.rstrip('/')}/api/v1/traces",
            json=payload,
            headers={
                "Content-Type": "application/json",
                "signoz-ingestion-key": settings.signoz_ingestion_key,
            },
            timeout=5.0,
        )
    except httpx.HTTPError:
        pass


@contextmanager
def stage_span(name: str, run_id: str, trace_id: str | None = None) -> Iterator[None]:
    start = perf_counter()
    try:
        print(f"span.start name={name} run_id={run_id} trace_id={trace_id}")
        yield
    finally:
        duration_ms = round((perf_counter() - start) * 1000, 2)
        print(
            f"span.end name={name} run_id={run_id} trace_id={trace_id} "
            f"duration_ms={duration_ms}"
        )
        _emit_sigNoz_span(name, run_id, trace_id, duration_ms)


def counter(name: str, value: int = 1, **labels: str) -> None:
    print(f"metric.counter name={name} value={value} labels={labels}")


def histogram(name: str, value: float, **labels: str) -> None:
    print(f"metric.histogram name={name} value={value} labels={labels}")

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
    # /v1/traces, NOT /api/v1/traces. The OTLP-over-HTTP path on the SigNoz
    # ingest host is /v1/traces; /api/v1/traces 404s. httpx does not raise on
    # a 404, so the old path failed silently and every span was dropped with
    # nothing in the logs to say so.
    try:
        response = httpx.post(
            f"{settings.signoz_ingest_base_url.rstrip('/')}/v1/traces",
            json=payload,
            headers={
                "Content-Type": "application/json",
                "signoz-ingestion-key": settings.signoz_ingestion_key,
            },
            timeout=5.0,
        )
        if response.status_code >= 400:
            print(f"span.export.failed status={response.status_code} name={name}")
    except httpx.HTTPError as exc:
        print(f"span.export.error name={name} error={type(exc).__name__}")


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

"""
forge/telemetry.py — OTel setup.

OWNER: DAMIR. This is the stub-session version: the signatures are frozen and
real, the exporter wiring is not mine to write. Damir replaces the internals
with the SigNoz OTLP exporter and the instrument list from his Block 1.

Frozen surface (§08) — keep these exactly:
    stage_span(name: str, run_id: str)          # context manager, yields a span
    counter(name: str, value: int = 1, **labels)
    histogram(name: str, value: float, **labels)

Two things this stub does that are worth keeping:

1. If opentelemetry is importable it uses the real tracer, so spans nest
   through OTel context automatically and land in SigNoz untouched.
2. If it is not, it falls back to printing an indented span tree to stderr.
   That means the "one parent span, eight nested children" check is verifiable
   on a laptop with no exporter configured — which is the state we are in at
   09:30. Set FORGE_TRACE_CONSOLE=0 to silence it, =1 to force it on.
"""
from __future__ import annotations

import contextlib
import contextvars
import os
import sys
import time

try:  # real telemetry if it is available
    from opentelemetry import metrics as _metrics
    from opentelemetry import trace as _trace

    _OTEL = True
except Exception:  # pragma: no cover - the pre-install path
    _OTEL = False

SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "forge-control")


def _configure_signoz() -> bool:
    """Point OTel at SigNoz Cloud when an ingestion key is configured.

    Uses http/protobuf rather than grpc on purpose: grpcio is a compiled
    dependency that fails to install on some machines, and the fallback costs
    us nothing here.
    """
    from forge import config

    if not (_OTEL and config.SIGNOZ_INGESTION_KEY):
        return False
    if os.getenv("FORGE_OTEL_CONFIGURED"):
        return True
    endpoint_base = os.getenv(
        "OTEL_EXPORTER_OTLP_ENDPOINT", f"https://ingest.{config.SIGNOZ_REGION}.signoz.cloud:443"
    ).rstrip("/")

    # Probe before wiring the exporter. A rejected key otherwise produces an
    # endless stream of "Failed to export span batch 401" from a background
    # thread, which buries every real log line during a demo.
    try:
        import httpx as _httpx

        probe = _httpx.post(
            f"{endpoint_base}/v1/traces",
            headers={"signoz-ingestion-key": config.SIGNOZ_INGESTION_KEY,
                     "Content-Type": "application/x-protobuf"},
            content=b"",
            timeout=8,
        )
        if probe.status_code in (401, 403):
            print(
                f"[telemetry] SigNoz rejected the ingestion key at {endpoint_base} "
                f"({probe.status_code}). Traces stay local. Check SIGNOZ_INGESTION_KEY and "
                "SIGNOZ_REGION (us | eu | in).",
                file=sys.stderr,
            )
            return False
    except Exception as exc:
        print(f"[telemetry] could not reach SigNoz ({exc}); traces stay local.", file=sys.stderr)
        return False

    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        endpoint = endpoint_base
        provider = TracerProvider(resource=Resource.create({"service.name": SERVICE_NAME}))
        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(
                    endpoint=f"{endpoint}/v1/traces",
                    headers={"signoz-ingestion-key": config.SIGNOZ_INGESTION_KEY},
                )
            )
        )
        _trace.set_tracer_provider(provider)
        os.environ["FORGE_OTEL_CONFIGURED"] = "1"
        return True
    except Exception as exc:  # telemetry must never take the factory down
        print(f"[telemetry] SigNoz export unavailable: {exc}", file=sys.stderr)
        return False


_SIGNOZ = _configure_signoz()

_depth: contextvars.ContextVar[int] = contextvars.ContextVar("forge_span_depth", default=0)
_tracer = _trace.get_tracer("forge") if _OTEL else None


def exporting() -> bool:
    """True when spans are actually leaving this process."""
    return bool(_SIGNOZ)
_meter = _metrics.get_meter("forge") if _OTEL else None
_instruments: dict[str, object] = {}


def _console_on() -> bool:
    flag = os.getenv("FORGE_TRACE_CONSOLE")
    if flag is not None:
        return flag not in ("0", "false", "False", "")
    return not _OTEL  # no exporter? then print, so the trace is still visible


def _emit(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


class _ConsoleSpan:
    """Minimal stand-in with the same surface the engine uses on a real span."""

    def __init__(self, name: str, run_id: str, depth: int):
        self.name = name
        self.run_id = run_id
        self.depth = depth
        self.attributes: dict = {"forge.run_id": run_id}
        self.events: list = []

    def set_attribute(self, key, value):
        self.attributes[key] = value

    def set_attributes(self, mapping):
        self.attributes.update(mapping)

    def add_event(self, name, attributes=None):
        self.events.append((name, attributes or {}))
        if _console_on():
            pad = "  " * (self.depth + 1)
            detail = " ".join(f"{k}={v}" for k, v in (attributes or {}).items())
            _emit(f"[trace] {pad}* {name} {detail}".rstrip())

    def record_exception(self, exc, attributes=None):
        self.add_event("exception", {"type": type(exc).__name__, "message": str(exc)})

    def set_status(self, *args, **kwargs):
        return None

    def is_recording(self) -> bool:
        return True


@contextlib.contextmanager
def stage_span(name: str, run_id: str):
    """Open a span. Nesting is by context: a span opened inside another is its child."""
    depth = _depth.get()
    token = _depth.set(depth + 1)
    started = time.perf_counter()
    console = _console_on()
    if console:
        _emit(f"[trace] {'  ' * depth}> {name}")

    if _OTEL:
        cm = _tracer.start_as_current_span(name)
    else:
        cm = contextlib.nullcontext(_ConsoleSpan(name, run_id, depth))

    span = None
    failed = False
    try:
        with cm as opened:
            span = opened
            if span is not None:
                try:
                    span.set_attribute("forge.run_id", run_id)
                    span.set_attribute("service.name", SERVICE_NAME)
                except Exception:
                    pass
            yield span
    except Exception:
        failed = True
        raise
    finally:
        took = (time.perf_counter() - started) * 1000
        if console:
            mark = "x" if failed else "+"
            attrs = ""
            if isinstance(span, _ConsoleSpan):
                shown = {
                    k: v
                    for k, v in span.attributes.items()
                    if k not in ("forge.run_id", "service.name", "forge.stage")
                }
                if shown:
                    attrs = "  " + " ".join(f"{k}={v}" for k, v in shown.items())
            _emit(f"[trace] {'  ' * depth}{mark} {name} ({took:.1f}ms){attrs}")
        _depth.reset(token)


def current_trace_id() -> str | None:
    """128-bit trace id as hex, for the Port -> SigNoz deep link. None if no OTel."""
    if not _OTEL:
        return None
    try:
        ctx = _trace.get_current_span().get_span_context()
        if not ctx or not ctx.trace_id:
            return None
        return format(ctx.trace_id, "032x")
    except Exception:
        return None


def counter(name: str, value: int = 1, **labels) -> None:
    if _OTEL:
        inst = _instruments.get(f"c:{name}")
        if inst is None:
            inst = _meter.create_counter(name)
            _instruments[f"c:{name}"] = inst
        inst.add(value, {k: str(v) for k, v in labels.items()})
    if os.getenv("FORGE_METRICS_CONSOLE") in ("1", "true"):
        _emit(f"[metric] {name} +{value} {labels}")


def histogram(name: str, value: float, **labels) -> None:
    if _OTEL:
        inst = _instruments.get(f"h:{name}")
        if inst is None:
            inst = _meter.create_histogram(name)
            _instruments[f"h:{name}"] = inst
        inst.record(value, {k: str(v) for k, v in labels.items()})
    if os.getenv("FORGE_METRICS_CONSOLE") in ("1", "true"):
        _emit(f"[metric] {name} = {value} {labels}")


def gauge(name: str, value: float, **labels) -> None:
    """forge_security_grade lives here. Damir: swap for an observable gauge."""
    if _OTEL:
        inst = _instruments.get(f"g:{name}")
        if inst is None:
            try:
                inst = _meter.create_gauge(name)
            except Exception:  # older SDKs
                inst = _meter.create_histogram(name)
            _instruments[f"g:{name}"] = inst
        inst.set(value, {k: str(v) for k, v in labels.items()}) if hasattr(inst, "set") else inst.record(
            value, {k: str(v) for k, v in labels.items()}
        )
    if os.getenv("FORGE_METRICS_CONSOLE") in ("1", "true"):
        _emit(f"[metric] {name} = {value} {labels}")

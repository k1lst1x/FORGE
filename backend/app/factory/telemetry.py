from collections.abc import Iterator
from contextlib import contextmanager
from time import perf_counter
from uuid import uuid4


def new_trace_id() -> str:
    return uuid4().hex


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


def counter(name: str, value: int = 1, **labels: str) -> None:
    print(f"metric.counter name={name} value={value} labels={labels}")


def histogram(name: str, value: float, **labels: str) -> None:
    print(f"metric.histogram name={name} value={value} labels={labels}")

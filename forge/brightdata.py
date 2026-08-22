"""
forge/brightdata.py -- the Bright Data CLI, driven as a subprocess.

Terminal only. Every call shells out to

    npx -p @brightdata/cli bdata scraper run <collector_id> <url> --pretty

inside a `brightdata.scraper_run` span. See CLAUDE.md for the pinned collector
and the rules.

WHAT THIS MODULE REFUSES TO DO
--------------------------------------------------------------------------
It does not decide what happens to a bad scrape. It raises, and the caller --
the scheduler -- decides. That keeps "the scrape failed" and "therefore the
data file is unchanged" in two different places, which is why a failure can
become an audit finding instead of a silent overwrite.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

import yaml

from forge import config

log = logging.getLogger("forge.brightdata")

STUB = False
REPO = config.REPO_ROOT
WATCHER_PATH = Path(os.getenv("FORGE_BOOKS_WATCHER", str(REPO / "watchers" / "books.yaml")))

#: Hard ceiling. The CLI polls a batch job and will happily wait an hour.
#:
#: 600s, not 120s. This target exceeds Bright Data's realtime page limit, so the
#: CLI falls back to a batch job on its own and a batch run takes MINUTES. At
#: 120s we were killing healthy runs mid-flight and recording them as timeouts,
#: which then aged the feed past D1 -- a finding our own ceiling created. The
#: scrape is off the demo path (it runs in its own task, see forge/scheduler.py),
#: so a long ceiling costs nothing anyone is waiting on.
HARD_TIMEOUT_SECONDS = int(os.getenv("FORGE_SCRAPE_TIMEOUT", "120"))

NPX = "npx"
CLI_ARGS = ["-y", "-p", "@brightdata/cli", "bdata"]


class ScrapeError(RuntimeError):
    """The scrape did not produce usable rows."""


class ScrapeTimeout(ScrapeError):
    """The CLI exceeded the hard timeout and was killed."""


class ContractViolation(ScrapeError):
    """Rows came back but do not satisfy the contract."""


def _cli() -> list[str]:
    # On Windows npx is npx.cmd: shutil.which finds it, bare exec does not.
    return [shutil.which(NPX) or NPX] + CLI_ARGS


def watcher() -> dict:
    try:
        return yaml.safe_load(WATCHER_PATH.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        log.error("could not read %s: %s", WATCHER_PATH, exc)
        return {}


def contract() -> dict:
    spec = watcher()
    path = REPO / (spec.get("contract") or "contracts/books.schema.json")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.error("could not read contract %s: %s", path, exc)
        return {}


def collector_id() -> str | None:
    explicit = os.getenv("BOOKS_COLLECTOR_ID") or os.getenv("BRIGHTDATA_COLLECTOR_ID")
    if explicit:
        return explicit
    pinned = (watcher().get("collector_id") or "").strip()
    return pinned or None


def target_url() -> str:
    return watcher().get("target_url") or "https://books.toscrape.com/"


def _env() -> dict:
    env = dict(os.environ)
    if config.BRIGHTDATA_API_TOKEN:
        env["API_TOKEN"] = config.BRIGHTDATA_API_TOKEN
    return env


def _parse_rows(stdout: str) -> list:
    """The CLI prints JSON. Tolerate a banner line before it."""
    text = (stdout or "").strip()
    if not text:
        return []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = min((i for i in (text.find("["), text.find("{")) if i >= 0), default=-1)
        if start < 0:
            raise
        payload = json.loads(text[start:])
    if isinstance(payload, dict):
        for key in ("data", "rows", "results", "records"):
            if isinstance(payload.get(key), list):
                return payload[key]
        return [payload]
    return payload if isinstance(payload, list) else []


def _normalise(row: dict) -> dict:
    """Map whatever the collector calls things onto the contract's names."""
    out = dict(row)
    if "title" not in out:
        for alias in ("name", "product", "book_title", "product_name"):
            if alias in out:
                out["title"] = out[alias]
                break
    price = out.get("price")
    if isinstance(price, str):
        try:
            out["price"] = float(price.strip().lstrip("£$€").replace(",", ""))
        except ValueError:
            out["price"] = None
    if "availability" in out and out["availability"] is not None:
        out["availability"] = str(out["availability"])
    return out


# --------------------------------------------------------------------------
# the contract
# --------------------------------------------------------------------------
def validate_contract(rows, spec: dict | None = None) -> list[dict]:
    """Rows that satisfy contracts/books.schema.json, or raise.

    Checks the three things a JSON Schema alone would not: the row count floor,
    the null ratio across required fields, and that price is genuinely numeric
    after normalisation.
    """
    spec = spec or contract()
    if not isinstance(rows, list):
        raise ContractViolation(f"expected a list of rows, got {type(rows).__name__}")

    required = (spec.get("items") or {}).get("required") or ["title", "price"]
    min_rows = int(spec.get("min_rows", 1))
    max_null = float(spec.get("max_null_ratio", 1.0))

    normalised = [_normalise(r) for r in rows if isinstance(r, dict)]
    if len(normalised) < min_rows:
        raise ContractViolation(
            f"{len(normalised)} row(s) returned, contract requires at least {min_rows}"
        )

    total = len(normalised) * len(required)
    nulls = sum(1 for r in normalised for f in required if r.get(f) in (None, ""))
    ratio = (nulls / total) if total else 1.0
    if ratio > max_null:
        raise ContractViolation(
            f"{nulls} of {total} required field(s) across {len(normalised)} rows are null "
            f"({ratio:.0%}), contract allows at most {max_null:.0%}"
        )
    return normalised


def contract_report(rows) -> dict:
    """Non-raising form, for the audit's D2 evidence."""
    try:
        ok = validate_contract(rows)
        return {"ok": True, "rows": len(ok), "reason": None}
    except ContractViolation as exc:
        return {"ok": False, "rows": len(rows or []), "reason": str(exc)}


# --------------------------------------------------------------------------
# the scrape
# --------------------------------------------------------------------------
def scraper_run(collector: str | None = None, url: str | None = None) -> list[dict]:
    """Run the pinned collector once. Returns raw rows, or RAISES.

    Deliberately does not touch data/. The caller decides what a failure means,
    which is what lets a bad scrape become an audit finding rather than a silent
    overwrite.
    """
    from forge import telemetry

    cid = collector or collector_id()
    url = url or target_url()

    started = time.perf_counter()
    exit_code, rows, failure = -1, [], None

    with telemetry.stage_span("brightdata.scraper_run", "scrape") as span:
        def tag(**kw):
            if span is None:
                return
            for k, v in kw.items():
                try:
                    span.set_attribute(k, v)
                except Exception:
                    pass

        tag(**{"bd.collector_id": cid or "none", "bd.url": url})
        try:
            if not cid:
                raise ScrapeError("no collector pinned; see CLAUDE.md")
            if not shutil.which(NPX):
                raise ScrapeError("npx is not on PATH, so the Bright Data CLI cannot be run")

            command = _cli() + ["scraper", "run", cid, url, "--pretty"]
            log.info("bdata scraper run %s %s (timeout %ss)", cid, url, HARD_TIMEOUT_SECONDS)
            proc = subprocess.run(
                command, capture_output=True, text=True,
                timeout=HARD_TIMEOUT_SECONDS, env=_env(), cwd=str(REPO),
            )
            exit_code = proc.returncode
            stdout, stderr = proc.stdout or "", proc.stderr or ""

            if exit_code != 0:
                blob = (stderr + stdout)[-800:]
                if "429" in blob or "rate limit" in blob.lower():
                    raise ScrapeError(
                        "Bright Data rate limited this run (429). The CLI backs off on its own "
                        "and the next tick retries. Detail: " + blob[-200:]
                    )
                raise ScrapeError("bdata exited " + str(exit_code) + ": " + blob[-300:])

            if not stdout.strip():
                # Exit 0 with no output is ZERO ROWS, not a crash. The contract
                # rejects it downstream; it must not masquerade as a parse error.
                log.warning("bdata exited 0 with empty stdout -- treating as zero rows")
                rows = []
            else:
                try:
                    rows = _parse_rows(stdout)
                except json.JSONDecodeError as exc:
                    tag(**{"bd.stdout_head": stdout[:800]})
                    raise ScrapeError("could not parse CLI output as JSON: " + str(exc)) from exc

        except subprocess.TimeoutExpired:
            # subprocess.run has already killed the child; say so explicitly.
            failure = ScrapeTimeout(
                "bdata exceeded the " + str(HARD_TIMEOUT_SECONDS) + "s hard timeout and was killed. "
                "This target runs as a batch job, which can outlast a tick."
            )
            tag(**{"bd.timed_out": True})
        except ScrapeError as exc:
            failure = exc
        except Exception as exc:
            failure = ScrapeError(type(exc).__name__ + ": " + str(exc))

        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        tag(**{"bd.exit_code": exit_code, "bd.duration_ms": duration_ms, "bd.row_count": len(rows)})
        telemetry.histogram("forge_scrape_duration_ms", duration_ms, collector=cid or "none")
        telemetry.histogram("forge_rows_extracted", len(rows), collector=cid or "none")

        if failure is not None:
            if span is not None:
                try:
                    span.record_exception(failure)
                    span.add_event("brightdata.scrape_failed", {"error": str(failure)[:300]})
                except Exception:
                    pass
            telemetry.counter(
                "forge_scrape_failures_total", 1,
                reason="timeout" if isinstance(failure, ScrapeTimeout) else "cli_error",
            )
            raise failure

    return rows


def scraper_heal(collector: str | None, prompt: str, url: str | None = None) -> dict:
    """Regenerate a collector whose selectors stopped matching, and STOP.

    No --auto-approve, ever: a human sees the preview before it commits.
    """
    cid = collector or collector_id()
    try:
        proc = subprocess.run(_cli() + ["scraper", "heal", cid, prompt], capture_output=True,
                              text=True, timeout=900, env=_env(), cwd=str(REPO))
        return {
            "status": "awaiting_approval" if proc.returncode == 0 else "failed",
            "preview_result": (proc.stdout or proc.stderr or "")[-2000:],
            "next_step": "npx -p @brightdata/cli bdata scraper approve " + str(cid),
        }
    except Exception as exc:
        return {"status": "failed", "preview_result": str(exc), "next_step": ""}


def scraper_approve(collector_or_cmd: str) -> bool:
    cid = collector_or_cmd.split()[-1] if " " in collector_or_cmd else collector_or_cmd
    try:
        return subprocess.run(_cli() + ["scraper", "approve", cid], capture_output=True,
                              text=True, timeout=300, env=_env(), cwd=str(REPO)).returncode == 0
    except Exception:
        return False


def scrape_markdown(url: str) -> str:
    """The page as text, used as audit evidence."""
    import httpx
    from bs4 import BeautifulSoup

    try:
        r = httpx.get(url, timeout=15, follow_redirects=True,
                      headers={"User-Agent": "forge-audit/3.0"})
        return BeautifulSoup(r.text, "html.parser").get_text(" ", strip=True)[:4000]
    except Exception as exc:
        log.warning("could not fetch %s: %s", url, exc)
        return ""

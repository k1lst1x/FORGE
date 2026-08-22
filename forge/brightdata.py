"""
forge/brightdata.py -- the Bright Data CLI, driven as a subprocess.

Terminal only. Every call here shells out to

    npx -p @brightdata/cli bdata scraper run <collector_id> <url>

inside a `brightdata.scraper_run` span. Nothing in this repo touches the
Bright Data dashboard: a pipeline that only works because someone clicked
something in a browser is not part of the factory.

The collector id is PINNED in CLAUDE.md and watchers/books.yaml. Generating one
takes 5-10 minutes, so it is never on the demo path.

WRITES ARE VALIDATED AND ATOMIC
--------------------------------------------------------------------------
A scrape that returns nothing, or rows missing the fields watchers/books.yaml
declares, does NOT overwrite data/books.json. Pulse keeps serving the last good
rows with an honest age on them, because stale-and-labelled beats empty-and-
silent. The write is temp-file-then-rename so a reader never sees half a file.

`last_success_at` is set only when validation passes. That is the single source
of freshness -- the previous implementation reported age from a local HTTP
cache, which reset to zero on refresh and made the counter run backwards.
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
DATA_PATH = Path(os.getenv("FORGE_BOOKS_DATA", str(REPO / "data" / "books.json")))
#: Resolved at call time: on Windows npx is npx.cmd, and subprocess cannot
#: exec the bare name even though shutil.which() finds it.
NPX = "npx"
CLI_ARGS = ["-y", "-p", "@brightdata/cli", "bdata"]


def _cli() -> list[str]:
    return [shutil.which(NPX) or NPX] + CLI_ARGS


class ScrapeError(RuntimeError):
    """The scrape did not produce usable rows. Existing data is left alone."""


def watcher() -> dict:
    try:
        return yaml.safe_load(WATCHER_PATH.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        log.error("could not read %s: %s", WATCHER_PATH, exc)
        return {}


def collector_id() -> str | None:
    """The pinned collector. Env wins so a demo machine can override."""
    explicit = os.getenv("BOOKS_COLLECTOR_ID") or os.getenv("BRIGHTDATA_COLLECTOR_ID")
    if explicit and not explicit.startswith("c_PENDING"):
        return explicit
    pinned = (watcher().get("collector_id") or "").strip()
    return pinned if pinned and not pinned.startswith("c_PENDING") else None


def target_url() -> str:
    return watcher().get("target_url") or "https://books.toscrape.com/"


# --------------------------------------------------------------------------
# validation -- rule 3 in CLAUDE.md
# --------------------------------------------------------------------------
def _coerce(row: dict, fields: list[dict]) -> dict | None:
    out = {}
    for field in fields:
        name = field["name"]
        value = row.get(name, field.get("default"))
        if value in (None, "") and field.get("required"):
            return None
        if field.get("type") == "number" and value is not None:
            try:
                value = float(str(value).strip().lstrip("£$€"))
            except (TypeError, ValueError):
                return None
        allowed = field.get("one_of")
        if allowed and value not in allowed:
            value = field.get("default", "unknown")
        out[name] = value
    return out


def validate(rows, spec: dict | None = None) -> list[dict]:
    """Rows that satisfy the watcher contract. Raises rather than writing junk."""
    spec = spec or watcher()
    fields = spec.get("fields") or []
    minimum = int((spec.get("validation") or {}).get("min_rows", 1))

    if not isinstance(rows, list):
        raise ScrapeError(f"expected a list of rows, got {type(rows).__name__}")

    clean = [c for c in (_coerce(r, fields) for r in rows if isinstance(r, dict)) if c]
    if len(clean) < minimum:
        raise ScrapeError(
            f"only {len(clean)} of {len(rows)} row(s) satisfied the field contract in "
            f"{WATCHER_PATH.name}; the minimum is {minimum}. Keeping the previous data."
        )
    return clean


# --------------------------------------------------------------------------
# the store -- atomic, with the last successful scrape recorded
# --------------------------------------------------------------------------
def read_data() -> dict:
    try:
        return json.loads(DATA_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"rows": [], "last_success_at": None, "source": None, "collector_id": None}


def _write_data(rows: list[dict], source: str, cid: str | None, duration_ms: float) -> dict:
    payload = {
        "rows": rows,
        "row_count": len(rows),
        "last_success_at": time.time(),
        "source": source,
        "collector_id": cid,
        "duration_ms": duration_ms,
    }
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = DATA_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(DATA_PATH)          # atomic: a reader never sees half a file
    return payload


def freshness() -> dict:
    """Age of the last SUCCESSFUL scrape. Never derived from a cache timestamp."""
    data = read_data()
    at = data.get("last_success_at")
    spec = watcher().get("validation") or {}
    max_age = int(spec.get("max_age_seconds", 3600))
    age = round(time.time() - at) if at else None
    return {
        "last_success_at": at,
        "age_seconds": age,
        "stale": age is None or age > max_age,
        "max_age_seconds": max_age,
        "source": data.get("source"),
        "collector_id": data.get("collector_id"),
        "rows": data.get("row_count", len(data.get("rows") or [])),
    }


# --------------------------------------------------------------------------
# the CLI, as a subprocess
# --------------------------------------------------------------------------
def _env() -> dict:
    """API_TOKEN reaches the CLI through the environment, never the argv line."""
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
            return []
        try:
            payload = json.loads(text[start:])
        except json.JSONDecodeError:
            return []
    if isinstance(payload, dict):
        for key in ("data", "rows", "results", "records"):
            if isinstance(payload.get(key), list):
                return payload[key]
        return [payload]
    return payload if isinstance(payload, list) else []


def scraper_run(collector: str | None = None, url: str | None = None) -> list[dict]:
    """Run the pinned collector once and write data/books.json if it validates.

    Returns the rows now being served -- the new ones on success, the previous
    good ones on failure. Never raises at the caller: Pulse must keep rendering.
    """
    from forge import telemetry

    cid = collector or collector_id()
    url = url or target_url()
    spec = watcher()
    timeout = int((spec.get("run") or {}).get("timeout_seconds", 300))

    started = time.perf_counter()
    exit_code, rows, error = -1, [], None

    with telemetry.stage_span("brightdata.scraper_run", "scrape") as span:
        try:
            if not cid:
                raise ScrapeError(
                    "no collector is pinned. Generate one, then set collector_id in "
                    "watchers/books.yaml and BOOKS_COLLECTOR_ID in CLAUDE.md."
                )
            if not shutil.which("npx"):
                raise ScrapeError("npx is not on PATH, so the Bright Data CLI cannot be run")

            command = _cli() + ["scraper", "run", cid, url, "--pretty"]
            if (spec.get("run") or {}).get("mode") == "sync":
                command.append("--sync")
            log.info("bdata: %s", " ".join(command[-5:]))

            proc = subprocess.run(command, capture_output=True, text=True,
                                  timeout=timeout, env=_env(), cwd=str(REPO))
            exit_code = proc.returncode
            if exit_code != 0:
                raise ScrapeError(f"bdata exited {exit_code}: {(proc.stderr or proc.stdout)[-300:]}")

            rows = validate(_parse_rows(proc.stdout), spec)
            duration = round((time.perf_counter() - started) * 1000, 1)
            _write_data(rows, url, cid, duration)
            log.info("scraped %s row(s) via %s in %sms", len(rows), cid, duration)

        except subprocess.TimeoutExpired:
            error = f"bdata did not finish within {timeout}s"
        except ScrapeError as exc:
            error = str(exc)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"

        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        if span is not None:
            span.set_attribute("bd.collector_id", cid or "none")
            span.set_attribute("bd.url", url)
            span.set_attribute("bd.exit_code", exit_code)
            span.set_attribute("bd.duration_ms", duration_ms)
            span.set_attribute("bd.row_count", len(rows))
            if error:
                span.set_attribute("bd.error", error[:300])
                span.add_event("brightdata.scrape_failed", {"error": error[:300]})
        telemetry.counter("forge_scrape_total", 1, result="ok" if not error else "failed")
        telemetry.histogram("forge_scrape_duration_ms", duration_ms)

    if error:
        # Keep serving the last good rows rather than blanking the app.
        previous = read_data().get("rows") or []
        log.error("scrape failed (%s); serving %s previous row(s)", error, len(previous))
        return previous
    return rows


def scraper_heal(collector: str | None, prompt: str, url: str | None = None) -> dict:
    """Regenerate a collector whose selectors stopped matching, and STOP.

    No auto-approve flag on purpose: a human sees the preview before it commits.
    """
    from forge import telemetry

    cid = collector or collector_id()
    with telemetry.stage_span("brightdata.scraper_heal", "heal") as span:
        try:
            proc = subprocess.run(_cli() + ["scraper", "heal", cid, prompt], capture_output=True,
                                  text=True, timeout=900, env=_env(), cwd=str(REPO))
            ok = proc.returncode == 0
            if span is not None:
                span.set_attribute("bd.collector_id", cid or "none")
                span.set_attribute("bd.exit_code", proc.returncode)
            return {
                "status": "awaiting_approval" if ok else "failed",
                "preview_result": (proc.stdout or proc.stderr or "")[-2000:],
                "next_step": f"npx -p @brightdata/cli bdata scraper approve {cid}",
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

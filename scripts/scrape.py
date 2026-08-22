"""
scripts/scrape.py -- one scrape, end to end, without make.

    python scripts/scrape.py              # --no-wait (the default)
    python scripts/scrape.py --wait       # block until the batch job lands

Runs the pinned collector through the Bright Data CLI, validates the rows
against contracts/books.schema.json, and writes data/books.json atomically.

WHY --no-wait IS THE DEFAULT
--------------------------------------------------------------------------
This listing exceeds Bright Data's realtime page limit, so the CLI falls back to
a BATCH job on its own and a batch run takes MINUTES, not seconds. Anything on
the demo path that blocks on one is a demo that hangs in front of a judge.

  --no-wait  starts the scrape in a detached child process and returns
             immediately. data/books.json keeps the rows it already has until
             the job lands, and the child's output goes to
             .forge_state/scrape.log so the run is still inspectable.
  --wait     blocks for up to FORGE_SCRAPE_TIMEOUT seconds (600 by default) and
             prints the rows. This is what the detached child itself runs, and
             what you want from a terminal when you actually want the answer.

Exit codes
  0  --no-wait: the batch job was launched · --wait: new rows were written
  1  --wait: contract or CLI failure (the previous data/books.json is kept)
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

#: Set on the detached child so it runs the scrape instead of detaching again.
CHILD_ENV = "FORGE_SCRAPE_CHILD"


def _arguments(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="scripts/scrape.py",
        description="Run the pinned Bright Data collector and write data/books.json.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--wait",
        dest="wait",
        action="store_true",
        help="block until the batch job finishes (up to the scrape timeout)",
    )
    group.add_argument(
        "--no-wait",
        dest="wait",
        action="store_false",
        help="start the batch job in the background and return immediately (default)",
    )
    parser.set_defaults(wait=False)
    return parser.parse_args(argv)


def _banner(bd, watcher: dict, mode: str) -> None:
    print()
    print("  collector : " + str(bd.collector_id()))
    print("  target    : " + bd.target_url())
    print("  contract  : " + str(watcher.get("contract")))
    print("  output    : " + str(watcher.get("output")))
    print("  mode      : " + str((watcher.get("run") or {}).get("mode", "async"))
          + " (batch -- this target exceeds the realtime page limit)")
    print("  timeout   : " + str(bd.HARD_TIMEOUT_SECONDS) + "s")
    print("  waiting   : " + mode)
    print()


# --------------------------------------------------------------------------
# --no-wait: hand the batch job to a child and get out of the way
# --------------------------------------------------------------------------
def _detach() -> int:
    """Start `scripts/scrape.py --wait` detached, print where to look, return.

    A detached CHILD rather than a thread: a thread either dies with this
    process (killing the scrape) or keeps it alive (the hang we are avoiding).
    The child outlives us and writes data/books.json when the job lands.
    """
    from forge import brightdata as bd
    from forge import config

    watcher = bd.watcher()
    _banner(bd, watcher, "no -- detached, this returns immediately")

    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    log_path = config.STATE_DIR / "scrape.log"

    environment = dict(os.environ, **{CHILD_ENV: "1"})
    creation = {}
    if os.name == "nt":
        creation["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        creation["start_new_session"] = True

    with log_path.open("a", encoding="utf-8") as handle:
        handle.write("\n=== scrape started %s ===\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
        handle.flush()
        child = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--wait"],
            cwd=str(REPO),
            env=environment,
            stdout=handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            **creation,
        )

    print("  LAUNCHED  batch job in pid " + str(child.pid) + ", up to "
          + str(bd.HARD_TIMEOUT_SECONDS) + "s")
    print("  output    " + str(log_path))
    print()
    print("  data/books.json is UNCHANGED until the job lands -- the previous rows")
    print("  keep serving, and the audit grades their freshness with D1 either way.")
    print("  Watch it with:  python scripts/scrape.py --wait")
    print()
    return 0


# --------------------------------------------------------------------------
# --wait: actually run it
# --------------------------------------------------------------------------
def _run() -> int:
    from forge import brightdata as bd
    from forge import store

    watcher = bd.watcher()
    _banner(bd, watcher, "yes -- this blocks until the batch job lands")

    started = time.time()
    try:
        raw = bd.scraper_run()
        print("  CLI returned " + str(len(raw)) + " raw row(s) in " + str(round(time.time() - started, 1)) + "s")
        rows = bd.validate_contract(raw)
    except bd.ScrapeTimeout as exc:
        print("  TIMEOUT: " + str(exc))
        print("  previous data/books.json is unchanged.")
        return 1
    except bd.ContractViolation as exc:
        print("  CONTRACT FAILED: " + str(exc))
        print("  previous data/books.json is unchanged -- the factory will raise D2.")
        return 1
    except bd.ScrapeError as exc:
        print("  SCRAPE FAILED: " + str(exc))
        print("  previous data/books.json is unchanged -- the factory will raise D1/D2.")
        return 1

    store.write_scrape(watcher, rows, contract_ok=True)
    age = store.scrape_age_seconds(watcher)

    print()
    print("  WROTE " + str(len(rows)) + " row(s) to " + str(watcher.get("output")))
    for row in rows[:3]:
        print("    " + str(row.get("title"))[:52].ljust(54)
              + str(row.get("price")).rjust(8) + "  " + str(row.get("availability")))
    print()
    print("  freshness : " + str(int(age or 0)) + "s since this scrape")
    print("  Pulse will serve these at / and /products")
    print()
    return 0


def main(argv=None) -> int:
    options = _arguments(argv)
    # A child is always the real thing, whatever it was passed.
    if options.wait or os.getenv(CHILD_ENV):
        return _run()
    return _detach()


if __name__ == "__main__":
    raise SystemExit(main())

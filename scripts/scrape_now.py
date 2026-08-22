"""
scripts/scrape_now.py -- one scrape, end to end. This is what `make scrape` runs.

Drives the pinned Bright Data collector through the CLI, validates the rows
against watchers/books.yaml, and writes data/books.json atomically. Prints what
Pulse will now serve and how old it is.

Exit codes: 0 wrote new rows · 1 kept the previous rows · 2 nothing to serve.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    from forge import brightdata as bd

    cid = bd.collector_id()
    print(f"\n  collector : {cid or 'NOT PINNED'}")
    print(f"  target    : {bd.target_url()}")
    print(f"  watcher   : {bd.WATCHER_PATH.name}")
    print(f"  output    : {bd.DATA_PATH}")

    if not cid:
        print("")
        print("  No collector pinned. Generate one, then set collector_id in")
        print("  watchers/books.yaml and BOOKS_COLLECTOR_ID in CLAUDE.md:")
        print("")
        hint = ("    npx -p @brightdata/cli bdata scraper create " + bd.target_url()
                + " " + chr(34) + "Extract every product: name, price as a number, "
                + "currency, availability" + chr(34) + " --name forge-books --pretty")
        print(hint)
        return 2

    before = bd.freshness()
    started = time.time()
    rows = bd.scraper_run()
    after = bd.freshness()

    took = round(time.time() - started, 1)
    wrote = after.get("last_success_at") != before.get("last_success_at")
    print(f"\n  {'WROTE' if wrote else 'KEPT PREVIOUS'}  {len(rows)} row(s) in {took}s")
    for row in rows[:5]:
        print(f"    {str(row.get('name'))[:46]:48} {row.get('currency','')} "
              f"{row.get('price')}  {row.get('availability')}")
    if len(rows) > 5:
        print(f"    ... {len(rows) - 5} more")

    age = after.get("age_seconds")
    print(f"\n  freshness : {age}s since the last SUCCESSFUL scrape"
          f"{' (STALE)' if after.get('stale') else ''}")
    print(f"  pulse will serve these rows at / and /products\n")
    return 0 if wrote else (1 if rows else 2)


if __name__ == "__main__":
    raise SystemExit(main())

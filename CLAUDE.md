# Bright Data in FORGE

Pulse renders scraped competitor data. Every scrape in this repo goes through
the Bright Data **CLI**, driven as a subprocess from inside the workflow.

## The pinned collector

```
BOOKS_COLLECTOR_ID = c_mt4y9wy817vo82ojy8          # set by scripts/bd_pin_collector.py
TARGET             = https://books.toscrape.com/
WATCHER            = watchers/books.yaml
OUTPUT             = data/books.json
```

## Usage string

Generating a collector takes 5-10 minutes. It is never on the demo path.

```bash
# Run the pinned collector (this is what `make scrape` does)
npx -p @brightdata/cli bdata scraper run $BOOKS_COLLECTOR_ID https://books.toscrape.com/ --pretty

# Create a collector -- ONCE, then pin the id above
npx -p @brightdata/cli bdata scraper create <url> "<field description>" --name <name> --pretty

# Heal a collector whose selectors stopped matching after a UI change
npx -p @brightdata/cli bdata scraper heal $BOOKS_COLLECTOR_ID "<what changed>"

# Approve a heal. Deliberately NOT automatic -- a human sees the preview first.
npx -p @brightdata/cli bdata scraper approve $BOOKS_COLLECTOR_ID
```

`API_TOKEN` comes from `BRIGHTDATA_API_TOKEN` in `.env` and is passed to the
subprocess environment. It is never written to a file or a command line.

## Rules

**1. Always use the CLI. Never the dashboard.**
The pipeline has to live inside the agentic workflow, not beside it. Every
Bright Data call in this repo is a subprocess from `forge/brightdata.py`,
inside a `brightdata.scraper_run` span. If a scrape only works because someone
clicked something in a browser, it is not part of the factory. Zero dashboard
use, all day.

**2. Reuse the pinned collector. Do not create one per run.**
`BOOKS_COLLECTOR_ID` above is the collector. Creating one takes 5-10 minutes,
so a `create` on the demo path is a demo that hangs. Generate ahead of time,
pin the id here, and commit it — that is what makes the configuration reusable
and version-controlled rather than a thing in someone's shell history.

**3. Validate before writing.**
A scrape result is only written to `data/books.json` if it parses, is a
non-empty list, and every row has the fields `watchers/books.yaml` declares.
A partial or empty result must NOT overwrite good data — Pulse keeps serving
the last good rows with an honest age on them. Writes are atomic: temp file,
then rename, so a reader never sees half a file.

**4. This target runs in batch mode, not realtime.**
The books listing exceeds Bright Data's realtime page limit, so `--sync` is
rejected and the CLI falls back to a batch job on its own. `watchers/books.yaml`
sets `mode: async` so the run does not pay for the failed realtime attempt
first. A batch job takes minutes -- another reason scraping is never on the
demo path.

**5. Freshness is the last SUCCESSFUL scrape.**
`last_success_at` in `data/books.json` is written only when validation passes.
Pulse reports age from that field and nothing else. It must never be derived
from a local cache timestamp, which resets on refresh and makes the age appear
to go backwards.

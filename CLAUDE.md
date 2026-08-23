# FORGE scraper rules

## Bright Data Scraper Studio

Always use the CLI. Never the web dashboard.

    alias: npx -p @brightdata/cli bdata

The pipeline has to live inside the agentic workflow, not beside it. Every
Bright Data call in this repo is a subprocess from `forge/brightdata.py`, inside
a `brightdata.scraper_run` span. A scrape that only works because someone
clicked something in a browser is not part of the factory.

`API_TOKEN` is passed to the subprocess environment from `BRIGHTDATA_API_TOKEN`
in `.env`. It never appears on a command line or in a file.

### Pinned collectors

    BOOKS_COLLECTOR_ID=c_mt4y9wy817vo82ojy8
    BOOKS_USAGE="bdata scraper run $BOOKS_COLLECTOR_ID https://books.toscrape.com/ --pretty"

### Rules

- Never create a new collector when a pinned one exists. Generation takes 5-10
  minutes, so a `create` on the demo path is a demo that hangs.
- Every scrape is validated against its contract before reaching `data/`.
  The contract is `contracts/books.schema.json`, referenced from
  `watchers/books.yaml`.
- On contract failure, do not hand-edit selectors — emit the finding and let
  the factory handle it. That is what D2 in the audit policy is for.
- Healing is human-gated: never pass `--auto-approve`. A person sees the
  preview before a regenerated collector commits.

### Known properties of this target

This listing exceeds Bright Data's realtime page limit, so `--sync` is refused
and the CLI falls back to a batch job on its own. `watchers/books.yaml` sets
`mode: async` to skip the wasted round trip. A batch run takes minutes, which
is another reason scraping is never on the demo path.

Everything below follows from "a batch run takes minutes", and the three
numbers have to stay consistent with each other:

| what | value | where | why |
| --- | --- | --- | --- |
| scrape timeout | 600s | `FORGE_SCRAPE_TIMEOUT`, `run.timeout_seconds` | 120s killed healthy batch runs and logged them as timeouts |
| scrape interval | 900s | `SCRAPE_INTERVAL_SECONDS`, `run.interval_seconds` | one scrape per 15 min, not one per audit tick |
| D1 freshness | 2400s | `max_age_seconds` | must outlast one interval **plus** one full batch run, or D1 fires on our own pipeline |

The audit keeps its own `AUDIT_INTERVAL_SECONDS` and is **decoupled** from all
three: it reads whatever is in `data/books.json` regardless of when that was
written. The scrape runs in its own daemon thread that no audit tick joins, and
if one is still in flight when the next tick fires the tick skips the scrape and
audits anyway — never two collectors at once, never a tick blocked on one.

`scripts/scrape.py` defaults to `--no-wait`: it hands the batch job to a
detached child, prints where the log is, and returns. `--wait` is the blocking
form, and it is what the child itself runs. Nothing on the demo path waits on a
batch job.

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

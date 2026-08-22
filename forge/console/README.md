# FORGE console

The operator front end for the factory. Buildless: no node_modules, no compile
step, no bundler. Open a URL and it runs.

```
forge/console/
  index.html      shell, palette, left rail, status-bar container
  app.js          router, polling, normalisers, all five screens
  chat-input.js   the brief composer (ported ai-chat-input)
  auth.js         Supabase Auth, optional
  config.js       the only file you edit
  demo-data.js    offline dataset, used when forge-control is unreachable
```

## Running it

Mounted by forge-control (the intended path):

```python
from fastapi.staticfiles import StaticFiles
app.mount("/console", StaticFiles(directory="forge/console", html=True), name="console")
```

`apiBase` in `config.js` is `''`, so a console served from the same origin calls
`/api/...` directly and needs no configuration.

Standalone, while forge-control is still being written:

```bash
python -m http.server 8099 --directory forge/console
# http://localhost:8099/          -> offline dataset
# http://localhost:8099/?api=http://localhost:8000   -> a forge-control elsewhere
```

Query overrides: `?api=<url>` · `?demo=1` force the offline dataset ·
`?nodemo=1` never fall back · `?noauth=1` skip the Supabase gate.

Keyboard: `1`–`5` switch screens, `R` forces a refresh. Screens are hash-routed
and deep-linkable: `#live #findings #runs #catalog #submit`.

## What the console asks forge-control for

Reads are ours, writes proxy to Port. Every normaliser is tolerant: it accepts
the `ChangeRequest` shape from `forge/models.py` as-is, accepts a bare array or
an envelope (`{runs: [...]}`, `{data: [...]}`), takes epoch seconds or ISO
strings for times, and renders "—" rather than breaking on a missing field. You
should not have to reshape anything to make a screen light up.

| Method | Path | Polled | Used by |
|---|---|---|---|
| GET | `/api/status` | 3s | status bar, rail, countdown |
| GET | `/api/runs/current` | 1s | Live |
| GET | `/api/findings` | 5s | Findings |
| GET | `/api/runs?limit=20` | 5s (on screen) | Runs |
| GET | `/api/catalog` | 10s (on screen) | Catalog |
| POST | `/api/brief` | — | Submit |
| POST | `/audit/run` | — | "Run audit now" |

`/api/status`

```json
{ "scheduler": "healthy", "next_audit_seconds": 47, "runs_today": 27,
  "severity": {"HIGH": 2, "MED": 1, "LOW": 2},
  "grades": {"/": "bronze", "/pricing": "silver"},
  "runs_per_hour": [1,0,2,3,1,4,2,5,3,2,6,4] }
```

`/api/runs/current` — `null` (or `{}`) when idle. Otherwise a `ChangeRequest`.
Three optional additions are worth sending because whole panels depend on them:

- `stages`: `{"TRIAGE": {"status": "done", "duration_ms": 7000, "attempts": 2}}`,
  status one of `done | active | skipped | pending`. Without it the console
  derives what it can from `stage` alone, and per-stage durations read "—".
- triage detail — `confidence`, `blast_radius`, `decided_by`, `model` — flat on
  the run or nested under `triage`. `decided_by` answers "which of these did the
  model actually decide?" and is rendered next to the verdict.
- `changeset[].added` / `.removed`. Given `{path, content, reason}` instead, the
  console shows the path and the reason rather than invented line counts.

`/api/findings` — a flat array. Extra keys used if present: `status`
(`open | fixing | suppressed`), `occurrences`, `justification` (shown inline in
italic on suppressed rows), `run_id`.

`/api/catalog` — `[{route, title, grade, high, med, last_audit, created_by_run,
page_id}]`.

`POST /api/brief` — body `{title, description, priority}` where priority is
`low | normal | high`. Returning `{"run_id": "..."}` lets the console jump
straight to Live and follow the new run.

## Behaviour worth knowing before you change it

- **Requests never stack.** One in-flight request per endpoint key, 8s abort.
- **A failed poll keeps the last good data** and raises an amber "reconnecting"
  pill. The screen is never blanked and a stack trace never reaches the user.
- **Offline fallback is labelled.** If `/api/status` cannot be reached on first
  load the console serves `demo-data.js` and shows a `DEMO DATA` pill for as
  long as it does. The status poll doubles as the liveness probe, so the console
  switches itself back to live data when forge-control comes up. Writes are
  refused in this mode rather than silently dropped.
- **Screens re-render only when their data changes.** Every screen has a
  signature; clocks (countdown, elapsed, active-stage duration) tick by writing
  `textContent` into existing nodes. Shimmer is first-load only. Nothing
  flickers while a judge is watching.
- **Approval is not in this console.** The gate panel links to Port and says on
  screen that the decision happens there. Do not add an approve button.

## Auth

Blank `supabaseUrl` / `supabaseAnonKey` in `config.js` and the console runs open,
with `auth · not configured` in the rail. Fill both and it gates on a session and
sends `Authorization: Bearer <access_token>` on every API call. A broken auth
config degrades to open mode with a console warning rather than a blank screen —
a missing key should never be the thing that stops a demo.

## The composer

`chat-input.js` is a vanilla port of the supplied `ai-chat-input` React
component: spring expand, auto-growing textarea with scroll fades, morphing
send/mic/stop button, dictation with a live level meter.

Two of the reference's controls are deliberately gone. The model picker would be
a lie (triage picks its own model server-side) and attachments would be a lie
(`/api/brief` takes text). The effort pill survived remapped onto something real
— the brief's priority. Dictation degrades honestly: no `SpeechRecognition` in
the browser means no mic button, never simulated words.

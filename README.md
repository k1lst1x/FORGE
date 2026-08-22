# FORGE ZERO DOWNTIME HACKATHON · SAT 22 AUG 2026 · BRIGHT DATA LOFT, SF

## FORGE

A factory that builds software, then finds the flaws in what it built and fixes them.

They asked: can you build the factory that builds the app?

Our answer is a factory whose relationship with the app doesn't end at shipping. It builds a feature, then audits it every five minutes, finds the security holes it left behind, writes the patch, proves the patch works, and stops for a human.

Building and repairing are the same code path here. Only the trigger differs.

## Hackathon Shape

FORGE is an agentic software factory for the Agent Harness Hackathon 2026. The app is the test run; the factory is the submission.

The first implementation is split into two independent workspaces:

- `backend/` - Python 3.12 + FastAPI API and factory orchestration surface.
- `frontend/` - React UI for operators to see builds, audits, fixes, and approvals.

The planned sponsor-tool loop:

- Port models briefs, services, approvals, workflow state, and human control points.
- Bright Data feeds the factory with live web data and scraper repair signals.
- SigNoz observes API calls, scraper jobs, audit runs, patch attempts, and approval gates.

## Stack Decisions

- Python: `3.12`
- Backend: FastAPI
- Django: not used initially; if we need Django later, prefer Django `5.2 LTS`.
- Frontend runtime: Node.js `24 LTS`
- Frontend: React `19.x` with Vite and TypeScript

React does not publish LTS releases in the same way as Node.js or Django, so the safe choice is the current stable major, pinned by the project lockfile once dependencies are installed.

## Repository Layout

```text
.
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── core/
│   │   └── main.py
│   ├── tests/
│   └── pyproject.toml
├── frontend/
│   ├── src/
│   ├── package.json
│   └── vite.config.ts
├── .nvmrc
├── .python-version
└── README.md
```

## Local Development

Backend:

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
fastapi dev app/main.py
```

The local operator login is `admin` / `forge-local`. For any shared or deployed
environment, set `FORGE_AUTH_USERNAME`, `FORGE_AUTH_PASSWORD`, and
`FORGE_AUTH_SECRET` in the environment before starting the backend. Factory API
routes require the bearer token returned by `POST /auth/login`; `/health` remains
public.

Frontend:

```bash
cd frontend
nvm use
npm install
npm run dev
```

## Initial API Contract

- `GET /health` - service health and project identity.
- `GET /factory/runs` - list persisted factory runs from SQLite.
- `POST /factory/runs` - create a factory run from a brief and, by default, execute the 8-step stub loop.
- `GET /factory/runs/{run_id}` - get one run with its steps and findings.
- `GET /factory/findings` - list audit findings.
- `POST /factory/audit/start` - start the scheduled audit loop.
- `POST /factory/audit/stop` - stop the scheduled audit loop.
- `GET /factory/audit/status` - inspect scheduler state.

These endpoints are deliberately small. They give the frontend something stable to call while the factory loop grows into real Port, Bright Data, GitHub, and SigNoz integrations.

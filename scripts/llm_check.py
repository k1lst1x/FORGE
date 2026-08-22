"""
scripts/llm_check.py -- verify the LLM setup before you need it.

    python scripts/llm_check.py

Checks, in order:
  1. a key is present for the selected provider
  2. the key authenticates
  3. both configured models actually exist on this account
  4. one real cheap call, priced, so you know what a run costs

Run it the moment you paste a key in. Discovering a wrong model id at 15:00,
mid-demo, from a 404 buried in a background task, is the failure this prevents.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    from forge import llm, planner, triage

    provider = llm.provider()
    key_name = llm.ENV_KEY[provider]
    print(f"\n  provider       {provider}")
    print(f"  triage model   {triage.MODEL}   (cheap: classification only)")
    print(f"  planner model  {planner.MODEL}   (writes code)")
    print(f"  budget         ${llm.budget_usd():.2f}\n")

    if not llm.credentials_available():
        print(f"  MISSING: {key_name} is not set. Add it to .env and re-run.\n")
        return 2
    print(f"  ok    {key_name} is set")

    if provider != "openai":
        print("  (model listing is only implemented for OpenAI)\n")
        return 0

    import openai

    client = openai.OpenAI()
    try:
        available = {m.id for m in client.models.list()}
        print(f"  ok    authenticated, {len(available)} models visible")
    except Exception as exc:
        print(f"  FAIL  could not authenticate: {type(exc).__name__}: {exc}\n")
        return 2

    problems = []
    for role, model in (("triage", triage.MODEL), ("planner", planner.MODEL)):
        if model in available:
            print(f"  ok    {role} model {model} exists")
        else:
            problems.append((role, model))
            close = sorted(m for m in available if m.startswith(model.split("-")[0]))[:6]
            print(f"  FAIL  {role} model {model} is NOT on this account")
            if close:
                print(f"        similar available: {', '.join(close)}")

    if problems:
        print("\n  Set FORGE_TRIAGE_MODEL / FORGE_PLANNER_MODEL in .env to ids that exist.\n")
        return 2

    print("\n  running one real triage call to price it...")
    try:
        result = triage.classify(
            {"check_id": "S12", "severity": "MED", "route": "/products",
             "title": "API documentation endpoint reachable in production mode",
             "evidence": "GET /docs returned 200 with an OpenAPI schema listing 11 endpoints",
             "suggested_fix_hint": "Guard the docs route behind settings.ENV == dev"},
            "<html><head><title>Pulse</title></head><body><h1>Pulse</h1></body></html>",
            {"pulse/main.py": "from fastapi import FastAPI\napp = FastAPI()\n"},
            [],
        )
        status = llm.budget_status()
        print(f"  ok    triage -> {result.classification} (decided by {result.decided_by})")
        print(f"        {result.tokens_in} in / {result.tokens_out} out, "
              f"${status['spend_usd']:.5f} spent")
        print(f"\n  {result.justification[:200]}\n")
        per_run = status["spend_usd"]
        if per_run:
            print(f"  At roughly ${per_run:.5f} per triage call, ${llm.budget_usd():.2f} buys "
                  f"about {int(llm.budget_usd() / max(per_run, 1e-9)):,} of them.")
        print("  Patch generation costs more -- watch /api/status while it runs.\n")
    except Exception as exc:
        print(f"  FAIL  the call did not complete: {type(exc).__name__}: {exc}\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

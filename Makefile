# FORGE
#
# NOTE: make is not installed on every machine in this team -- every target
# below is a one-line wrapper around a python entry point that works without
# it. If make is missing, run the command in the recipe directly.
#
# Damir owns `up`. Everything else here is Rohit's.

PY ?= python
MODE ?= 1
ROUTES ?= / /products
URL ?= http://localhost:8100

.PHONY: help up audit brief inject restore inject-status inject-smoke triage-smoke test trace clean security-preview

help:
	@echo "  make audit                 run the 17 checks once against $(URL)"
	@echo "  make inject MODE=1|2|3|4   inject a defect a judge picked"
	@echo "  make restore               put everything back"
	@echo "  make inject-status         what is currently injected"
	@echo "  make inject-smoke          stop/restore Pulse 10x and check triage every time"
	@echo "  make triage-smoke          exercise all five triage classifications"
	@echo "  make brief BRIEF='...'     run Loop A from a brief"
	@echo "  make test                  the factory's own test suite"

# --- Damir: forge-control + pulse + cloudflared in one command ---
up:
	@echo "TODO(damir): start forge-control, pulse and cloudflared with labelled output"

audit:
	$(PY) scripts/audit_now.py --url $(URL) --routes $(ROUTES) --quiet

brief:
	$(PY) scripts/demo_run.py --brief "$(BRIEF)"

inject:
	$(PY) -m forge.inject $(MODE)

restore:
	$(PY) -m forge.inject --restore

inject-status:
	$(PY) -m forge.inject --status

inject-smoke:
	$(PY) scripts/inject_smoke.py --repeat 10

triage-smoke:
	$(PY) scripts/triage_smoke.py --repeat 10 --quiet

test:
	$(PY) -m pytest tests/ -q

trace:
	@echo "open the run's trace_id in SigNoz -- printed by every run"

clean:
	$(PY) -m forge.inject --restore || true
	rm -rf .forge_inject .pytest_cache

security-preview:
	$(PY) scripts/security_preview.py --url $(URL)

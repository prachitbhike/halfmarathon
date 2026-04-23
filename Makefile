.PHONY: install fixtures clock-test test lint typecheck \
        sandbox-base sandbox-langgraph sandbox-temporal-pydantic sandbox-letta sandbox-claude-sdk \
        eval report help

UV := uv

help:
	@echo "halfmarathon — long-running agent comparison"
	@echo
	@echo "Phase 0 (foundations):"
	@echo "  make install              # uv sync"
	@echo "  make clock-test           # smoke test the fixture clock"
	@echo "  make test                 # pytest"
	@echo "  make lint                 # ruff check"
	@echo "  make typecheck            # mypy"
	@echo "  make sandbox-base         # build the e2b base image"
	@echo
	@echo "Phase 1+ (per-impl):"
	@echo "  make sandbox-langgraph"
	@echo "  make sandbox-temporal-pydantic"
	@echo "  make sandbox-letta"
	@echo "  make sandbox-claude-sdk"
	@echo
	@echo "Phase 2+ (eval):"
	@echo "  make eval                 # run dimension tests"
	@echo "  make report               # generate comparison matrix"

install:
	$(UV) sync --extra dev

clock-test:
	$(UV) run python -m task.clock_smoke

test:
	$(UV) run pytest -q

lint:
	$(UV) run ruff check .

typecheck:
	$(UV) run mypy task

# --- sandboxes -----------------------------------------------------------

sandbox-base:
	cd infra/e2b/base && e2b template build

sandbox-langgraph: sandbox-base
	cd infra/e2b/langgraph && e2b template build

sandbox-temporal-pydantic: sandbox-base
	cd infra/e2b/temporal-pydantic && e2b template build

sandbox-letta: sandbox-base
	cd infra/e2b/letta && e2b template build

sandbox-claude-sdk: sandbox-base
	cd infra/e2b/claude-sdk && e2b template build

# --- eval (Phase 2+) -----------------------------------------------------

eval:
	$(UV) run python -m eval.harness

report:
	$(UV) run python -m eval.report

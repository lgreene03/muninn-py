# muninn-py developer tasks.
# One-liners that mirror what CI runs (.github/workflows/ci.yml).

.DEFAULT_GOAL := help
.PHONY: help dev lint format typecheck test test-all docs docs-serve smoke clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

dev: ## Install the package in editable mode with all dev extras
	python -m pip install --upgrade pip
	pip install -e ".[dev,notebooks,cache,dashboard]"

lint: ## Lint with ruff (matches CI)
	ruff check .

format: ## Auto-fix lint + format with ruff
	ruff check --fix .
	ruff format .

typecheck: ## Type-check the package with mypy --strict
	mypy src/muninn

test: ## Run the unit-test suite (integration tests excluded by default)
	pytest -q

test-all: ## Run every test including Docker-backed integration tests
	pytest -q -m "integration or not integration"

docs: ## Build the docs site in strict mode (fails on broken nav/refs)
	mkdocs build -s

docs-serve: ## Serve the docs locally with live reload
	mkdocs serve

smoke: ## Run the CLI smoke test against a running server
	bash scripts/smoke.sh

clean: ## Remove build artifacts and caches
	rm -rf build dist site .pytest_cache .mypy_cache .ruff_cache *.egg-info src/*.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +

check: lint typecheck test ## Run lint + typecheck + tests (the pre-push gate)
.PHONY: check

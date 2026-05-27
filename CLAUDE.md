# CLAUDE.md

## What Is muninn-py

Python research SDK and CLI for the Muninn feature computation platform. Pulls deterministic features from Muninn's Query API into Polars/pandas DataFrames for notebook-driven alpha research.

## Commands

```bash
# Install in development mode
pip install -e ".[dev,notebooks]"

# Run unit tests (no Docker needed)
pytest

# Run integration tests (requires Docker — boots full Muninn stack)
pytest -m integration -v

# Type checking
mypy src/

# Lint
ruff check src/ tests/

# Build docs site
mkdocs serve

# Smoke test (validates CLI + synthetic trade against running server)
bash scripts/smoke.sh

# CLI examples
muninn features list
muninn features get vwap.1m --instrument BTC-USDT --start 2026-05-10T14:00:00Z --end 2026-05-10T15:00:00Z
muninn replay submit --start 2026-05-10T14:00:00Z --end 2026-05-10T15:00:00Z
muninn dashboard  # Streamlit researcher dashboard (requires [dashboard] extra)
```

## Package Structure

- `src/muninn/` — Main package
  - `client.py` — `MuninnClient` (sync, httpx-backed)
  - `async_client.py` — `AsyncMuninnClient` (async sibling)
  - `models.py` — Pydantic v2 models: `FeatureValue`, `FeatureDefinition`, `ReplayJob`
  - `exceptions.py` — `MuninnError` hierarchy: `MuninnAPIError`, `MuninnNotFoundError`, `MuninnValidationError`, `MuninnTimeoutError`
  - `retry.py` — `RetryConfig` for exponential backoff
  - `cache.py` — Optional disk cache for closed event-time windows
  - `notebook.py` — Research helpers: `forward_returns`, `information_coefficient`, `rolling_corr`, `hit_rate`
  - `cli/` — Click-based CLI (`muninn features`, `muninn replay`, `muninn dashboard`)
  - `dashboard/` — Streamlit researcher dashboard
  - `_pandas.py` — `.pandas` accessor returning pandas DataFrames

## Testing

- **Unit tests** (`tests/`): 95+ tests using `respx` to mock the Muninn HTTP API. No server needed.
- **Integration tests** (`tests/test_integration.py`): 27 Testcontainers tests that boot the full Muninn stack. Marked `@pytest.mark.integration`, excluded by default. Run with `pytest -m integration`.
- **OpenAPI contract tests** (`tests/test_openapi_contract.py`): 15 offline tests against a recorded spec snapshot.
- **Benchmarks** (`tests/bench_client.py`): pytest-benchmark baselines.

## Norse Stack Context

muninn-py is the research client in the four-service Norse stack:

```
Muninn (server) ← muninn-py (this SDK) ← Researcher's notebook
```

The SDK never talks to Huginn or Sleipnir — only to Muninn's Query API at `/api/v1/features/*` and `/api/v1/replay/*`.

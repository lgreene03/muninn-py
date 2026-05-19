# Changelog

All notable changes to `muninn-py` are documented in this file. Format follows [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/); versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Phase C — Production readiness (complete).** Three additions; no existing call site changes.
  - **Retry with exponential backoff.** `RetryConfig` exposed at package root: `max_attempts` (default 3), `initial_backoff`, `max_backoff`, `backoff_factor`, `jitter`, `retry_statuses` (default 408/429/500/502/503/504). Retries the configured 5xx and transport exceptions (`ConnectError`, `TimeoutException`, `RemoteProtocolError`, `ReadError`, `WriteError`, `PoolTimeout`). Never retries 4xx or already-decoded responses. Sync uses `time.sleep`; async uses `asyncio.sleep`; policy is shared. Disable with `RetryConfig(max_attempts=1)`.
  - **Per-operation timeouts and connection-pool tunables.** Constructor `timeout` now accepts `httpx.Timeout(connect=, read=, write=, pool=)` in addition to a single `float`. New `max_connections`, `max_keepalive_connections`, `keepalive_expiry` constructor kwargs.
  - **Optional disk cache.** Install with `pip install 'muninn-py[cache]'`. Set `MuninnClient(cache_dir="...")` to cache `get_feature` responses for closed event-time windows on local disk via `diskcache`. Open windows (`end > now`) are never cached. `client.clear_cache()` drops everything; survives process restart. Cache does not version on `code_version` — operator's responsibility to clear after a server upgrade.
  - 23 new tests (72 → **95 total**, all green on every supported Python).
- **Phase B-5 — Pandas-first accessor (Phase B complete).** New `.pandas` property on both `MuninnClient` and `AsyncMuninnClient`. Mirror surface — `get_feature`, `get_features`, `get_panel` — but every method returns a `pandas.DataFrame` instead of a Polars one. Lazy-imported and cached per-client so Polars users pay no cost. Conversion is pyarrow-free (uses `to_dicts()` round-trip), so the SDK keeps its hard dependency set narrow. New dev dep `pandas-stubs` for mypy. 6 new tests; total **72 unit tests, all green**.
- **Phase B-4 — `muninn` CLI.** Click-based shell entry point installed as the `muninn` command. Subcommands: `muninn features list`, `muninn features get <name>`, `muninn replay submit`, `muninn replay status <id>`, `muninn replay list`. JSON output by default (pipe-friendly), `--format table` for human display. Host override via `--host` or `MUNINN_HOST` env var. 8 new tests using Click's `CliRunner`. Total 66 unit tests, all green. New dependency: `click>=8.1,<9`.
- **Phase B-3 — `muninn.notebook` helpers.** Pure-function helpers for the most common research one-liners over a Muninn feature panel: `forward_returns` (log or simple, multi-period), `information_coefficient` (Spearman or Pearson per signal vs. a return column), `rolling_corr`, and `hit_rate`. Polars in, Polars out, no mutation, no wall-clock reads — matching the determinism discipline of the server. 15 new tests; total 58 unit tests, all green.
- **Phase B-2 — Multi-instrument `get_panel`.** New method on both clients: `get_panel(instruments=[...], features=[...], start, end)`. Returns a long-form Polars DataFrame with columns `instrument`, `event_time`, then one per feature, sorted by `(instrument, event_time)`. Sync path fans out across the thread pool; async path uses `asyncio.gather`. 6 new tests; total 43 unit tests, all green.
- **Phase B-1 — `AsyncMuninnClient` and parallel feature fetches.** New `AsyncMuninnClient` mirrors the sync client's surface using `httpx.AsyncClient`. Multi-feature `get_features` calls fan out concurrently via `asyncio.gather` on the async path and via a thread pool on the sync path; `parallel=False` forces serial. Shared transport helpers extracted to `_transport.py` so error mapping and frame construction stay identical. 13 new async tests with `respx`; total now 37 unit tests, all green.
- `docs/ROADMAP.md` — six-phase delivery plan mirroring the server's discipline. Phase A foundations marked complete; B–F mapped with deliverables, exit criteria, and rationale.
- Dependabot configuration for `pip` (grouped runtime + dev) and GitHub Actions.
- Release workflow (`.github/workflows/release.yml`) publishing to PyPI via Trusted Publishing (OIDC). Manual dispatch supports a TestPyPI dry-run.
- `docs/RELEASING.md` — one-time PyPI Trusted Publisher setup + the cut-a-release flow.
- Initial bootstrap of the SDK.
- `MuninnClient` — synchronous `httpx`-backed client with context-manager lifecycle.
- `MuninnClient.list_features()` — discover registered feature schemas.
- `MuninnClient.get_feature()` — fetch a single feature's time-series as a Polars DataFrame.
- `MuninnClient.get_features()` — fetch multiple features and outer- or inner-join on `event_time`.
- `MuninnClient.list_replay_jobs()` / `get_replay_job()` / `submit_replay_job()` — replay-job orchestration from a notebook.
- Typed pydantic models: `FeatureValue`, `FeatureDefinition`, `ReplayJob`, `ReplayJobSubmission`, `ReplayJobStatus`.
- Typed exception hierarchy: `MuninnError` → `MuninnAPIError`, `MuninnNotFoundError`, `MuninnValidationError`, `MuninnTimeoutError`.
- Sample notebook `notebooks/alpha_backtest_demo.ipynb` — pull features, compute forward returns and IC, plot, then submit a replay to demonstrate the determinism property.
- Test suite using `respx` (HTTP mocking) — contract tests for models, behavior tests for the client, error-mapping coverage.
- `pyproject.toml` (setuptools + PEP 621), `ruff`, `mypy` strict, `pytest` configuration.
- GitHub Actions CI: lint + type-check + tests on Python 3.10 / 3.11 / 3.12.

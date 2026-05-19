# Changelog

All notable changes to `muninn-py` are documented in this file. Format follows [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/); versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
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

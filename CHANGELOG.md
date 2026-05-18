# Changelog

All notable changes to `muninn-py` are documented in this file. Format follows [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/); versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
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

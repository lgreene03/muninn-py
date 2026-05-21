# muninn-py

[![PyPI](https://img.shields.io/pypi/v/muninn-py)](https://pypi.org/project/muninn-py/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://github.com/lgreene03/muninn-py/blob/main/LICENSE)
[![CI](https://github.com/lgreene03/muninn-py/actions/workflows/ci.yml/badge.svg)](https://github.com/lgreene03/muninn-py/actions/workflows/ci.yml)

**Python research SDK for [Muninn](https://github.com/lgreene03/muninn)** — an event-native market-data feature computation platform that emphasises deterministic replay and live/historical parity.

`muninn-py` is the notebook-side companion. It pulls features computed by a running Muninn `query-api` into [Polars](https://pola.rs/) or [pandas](https://pandas.pydata.org/) DataFrames, with a typed client that maps the server's contracts to pydantic models.

## Quick example

```python
from muninn import MuninnClient

with MuninnClient() as m:
    df = m.get_features(
        instrument="BTC-USDT",
        features=["vwap.1m", "obi", "vpin"],
        start="2026-05-10T14:00:00Z",
        end="2026-05-10T15:00:00Z",
    )
    df.head()
```

## Key features

- **Typed responses** via pydantic — `FeatureValue`, `FeatureDefinition`, `ReplayJob`.
- **Polars-first**, pandas-friendly. Switch with `.to_pandas()` at the boundary.
- **Multi-feature joins on `event_time`** in one call (`get_features`); outer or inner.
- **Replay-job orchestration** — submit, poll, and reason about a backtest from the notebook.
- **Typed exception hierarchy** — `MuninnNotFoundError`, `MuninnValidationError`, `MuninnTimeoutError`, `MuninnAPIError`.
- **Async client** (`AsyncMuninnClient`) for high-throughput research pipelines.
- **Offline caching** via `diskcache` to avoid hammering the server during iterative exploration.

## Links

- **Source code** — [github.com/lgreene03/muninn-py](https://github.com/lgreene03/muninn-py)
- **Server** — [github.com/lgreene03/muninn](https://github.com/lgreene03/muninn)
- **Strategy execution** — [github.com/lgreene03/huginn](https://github.com/lgreene03/huginn)

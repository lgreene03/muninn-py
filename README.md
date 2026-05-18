# muninn-py

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

**Python research SDK for [Muninn](https://github.com/lgreene03/muninn)** — an event-native market-data feature computation platform that emphasises deterministic replay and live/historical parity.

`muninn-py` is the notebook-side companion. It pulls features computed by a running Muninn `query-api` into [Polars](https://pola.rs/) or [pandas](https://pandas.pydata.org/) DataFrames, with a typed client that maps the server's contracts to pydantic models.

## What you get

```python
from muninn import MuninnClient

with MuninnClient() as m:                        # zero config: defaults to http://localhost:8080
    df = m.get_features(
        instrument="BTC-USDT",
        features=["vwap.1m", "obi", "vpin"],
        start="2026-05-10T14:00:00Z",
        end="2026-05-10T15:00:00Z",
    )
    df.head()                                    # Polars DataFrame indexed by event_time
```

- **Typed responses** via pydantic — `FeatureValue`, `FeatureDefinition`, `ReplayJob`.
- **Polars-first**, pandas-friendly. Switch with `.to_pandas()` at the boundary.
- **Multi-feature joins on `event_time`** in one call (`get_features`); outer or inner.
- **Replay-job orchestration** — submit, poll, and reason about a backtest from the notebook.
- **Typed exception hierarchy** — `MuninnNotFoundError`, `MuninnValidationError`, `MuninnTimeoutError`, `MuninnAPIError`.

## Install

```bash
pip install muninn-py
# or, with notebook extras:
pip install "muninn-py[notebooks]"
```

Python 3.10+ is required.

## Quickstart

1. Start a Muninn server locally. From the [main repo](https://github.com/lgreene03/muninn):

   ```bash
   docker compose up -d --wait
   ./scripts/smoke.sh
   ```

   The Query API listens on `http://localhost:8080` by default.

2. From a Python shell or Jupyter:

   ```python
   from muninn import MuninnClient

   with MuninnClient() as m:
       for feat in m.list_features():
           print(feat.name, feat.version)
   ```

3. Run the bundled notebook for an end-to-end demo (signal IC + replay):

   ```bash
   jupyter lab notebooks/alpha_backtest_demo.ipynb
   ```

## API

| Method | Returns | Description |
|---|---|---|
| `MuninnClient(host="...", timeout=30.0, headers=None)` | client | Construct a sync client. Use as a context manager to auto-close. |
| `list_features()` | `list[FeatureDefinition]` | Discover registered feature schemas. |
| `get_feature(name, *, instrument, start, end, limit=None)` | `pl.DataFrame` | One feature's time-series, sorted by `event_time`. |
| `get_features(instrument, features, start, end, *, limit=None, join="outer")` | `pl.DataFrame` | Multi-feature panel; joined on `event_time`. |
| `submit_replay_job(*, start, end, topics=None, feature_version=None)` | `ReplayJob` | Submit a new replay; returns the initial `PENDING` state. |
| `get_replay_job(job_id)` | `ReplayJob` | Poll a single job's status. |
| `list_replay_jobs()` | `list[ReplayJob]` | All jobs the server is currently tracking. |

`start` and `end` accept either ISO-8601 strings (`"2026-05-10T14:00:00Z"`) or `datetime` instances.

## Why "zero configuration"

Defaults match the Muninn server's local profile (`http://localhost:8080`, no auth). For deployments behind a reverse proxy, override:

```python
client = MuninnClient(
    host="https://muninn.example.internal",
    timeout=60.0,
    headers={"Authorization": "Bearer <token>"},
)
```

Authentication itself is an operator concern on the server side — see [SECURITY_MODEL.md](https://github.com/lgreene03/muninn/blob/main/docs/steering/SECURITY_MODEL.md) on the main repo.

## Determinism guarantee — what this SDK preserves

Any value you pull through `get_feature` was emitted by a pure-function computer in the Muninn server (see [DETERMINISTIC_REPLAY.md](https://github.com/lgreene03/muninn/blob/main/docs/steering/DETERMINISTIC_REPLAY.md)). The SDK does not transform values — it deserializes, joins, and sorts. A replay of the same input range through the same `feature_version` returns identical numbers. Notebook reproducibility follows from that property as long as you record `(start, end, feature_version)` for any number you report.

Per [ADR-0002](https://github.com/lgreene03/muninn/blob/main/docs/adr/0002-event-id-determinism.md), `event_id` is provenance metadata and may differ across runs; the computational fields are what the determinism claim covers.

## Development

```bash
git clone https://github.com/lgreene03/muninn-py.git
cd muninn-py
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,notebooks]"
pytest
```

The test suite uses [respx](https://lundberg.github.io/respx/) to mock the Muninn HTTP API — no running server required.

## Non-goals

`muninn-py` is intentionally narrow:

- **Not a backtesting framework.** It is a data-access library. Use it inside whatever research framework you prefer.
- **Not a trading client.** No order routing, no execution, no portfolio state. The Muninn server itself is also not these things — see [NON_GOALS.md](https://github.com/lgreene03/muninn/blob/main/docs/steering/NON_GOALS.md).
- **Not a streaming client (yet).** Polling-and-DataFrame is the primary mode. A streaming/async path is a possible follow-up if a real use case appears.

## License

[Apache 2.0](LICENSE). See [NOTICE](NOTICE) on the main repo for attribution.

## Related

- [Muninn](https://github.com/lgreene03/muninn) — the server / platform this SDK targets.

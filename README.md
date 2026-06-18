# muninn-py

[![CI](https://github.com/lgreene03/muninn-py/actions/workflows/ci.yml/badge.svg)](https://github.com/lgreene03/muninn-py/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

**Python research SDK for [Muninn](https://github.com/lgreene03/muninn)** — an event-native market-data feature computation platform that emphasises deterministic replay and live/historical parity. Part of the **[Norse Stack](https://github.com/lgreene03/norse-stack)**.

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
| `MuninnClient(host="...", timeout=30.0, headers=None, max_workers=None)` | client | Construct a sync client. Use as a context manager to auto-close. |
| `list_features()` | `list[FeatureDefinition]` | Discover registered feature schemas. |
| `get_feature(name, *, instrument, start, end, limit=None)` | `pl.DataFrame` | One feature's time-series, sorted by `event_time`. |
| `get_features(instrument, features, start, end, *, limit=None, join="outer", parallel=True)` | `pl.DataFrame` | Multi-feature panel; joined on `event_time`. Fans out across a thread pool when `parallel=True` (default). |
| `get_panel(instruments, features, start, end, *, limit=None, join="outer", parallel=True)` | `pl.DataFrame` | Multi-instrument, multi-feature panel. Long-form: columns are `instrument`, `event_time`, then one per feature. |
| `submit_replay_job(*, start, end, topics=None, feature_version=None)` | `ReplayJob` | Submit a new replay; returns the initial `PENDING` state. |
| `get_replay_job(job_id)` | `ReplayJob` | Poll a single job's status. |
| `list_replay_jobs()` | `list[ReplayJob]` | All jobs the server is currently tracking. |

`start` and `end` accept either ISO-8601 strings (`"2026-05-10T14:00:00Z"`) or `datetime` instances.

### CLI

Installed automatically with the package:

```bash
muninn features list
muninn features get vwap.1m \
    --instrument BTC-USDT \
    --start 2026-05-10T14:00:00Z \
    --end   2026-05-10T15:00:00Z
muninn replay submit --start 2026-05-10T14:00:00Z --end 2026-05-10T15:00:00Z
muninn replay status <jobid>
muninn stream listen --feature vwap.1m
```

Default host is `http://localhost:8080`; override with `--host` or `MUNINN_HOST`. Output is JSON by default — composable with `jq` and shell pipelines — with `--format table` for human-readable display.

### Notebook helpers

```python
from muninn import MuninnClient
from muninn.notebook import forward_returns, information_coefficient

with MuninnClient() as m:
    df = m.get_features(
        instrument="BTC-USDT",
        features=["vwap.1m", "obi", "vpin"],
        start="2026-05-10T14:00:00Z",
        end="2026-05-10T18:00:00Z",
    )

df = forward_returns(df, price_col="vwap.1m", periods=[1, 5])
ic = information_coefficient(df, signals=["obi", "vpin"], return_col="fwd_return_1")
```

Pure functions, Polars-in/Polars-out, no wall-clock reads. Includes `forward_returns`, `information_coefficient`, `rolling_corr`, `hit_rate`.

### Resilient by default

The clients retry transient failures (5xx, connection errors, timeouts) with exponential backoff and disable that behaviour when you want it disabled:

```python
from muninn import MuninnClient, RetryConfig

with MuninnClient(retry=RetryConfig(max_attempts=5, initial_backoff=0.5)) as m:
    ...

# Disable retry entirely:
with MuninnClient(retry=RetryConfig(max_attempts=1)) as m:
    ...
```

For finer-grained control of how long each phase of an HTTP call is allowed to take, pass an `httpx.Timeout`:

```python
import httpx

client = MuninnClient(timeout=httpx.Timeout(connect=2.0, read=30.0, write=10.0, pool=5.0))
```

Connection-pool tunables are exposed for operators fronting the API behind a load balancer:

```python
client = MuninnClient(
    max_connections=50,
    max_keepalive_connections=10,
    keepalive_expiry=2.0,
)
```

### Disk-cache for closed windows

Feature time-series over closed event-time windows are deterministic on the server side. Opt into a local on-disk cache so notebook iteration doesn't re-fetch the same range every time:

```bash
pip install 'muninn-py[cache]'
```

```python
with MuninnClient(cache_dir="~/.muninn/cache") as m:
    df = m.get_feature(
        "vwap.1m", instrument="BTC-USDT",
        start="2026-05-10T14:00:00Z", end="2026-05-10T15:00:00Z",
    )                                     # one HTTP call
    df = m.get_feature(
        "vwap.1m", instrument="BTC-USDT",
        start="2026-05-10T14:00:00Z", end="2026-05-10T15:00:00Z",
    )                                     # cache hit, no HTTP
```

The cache:

- Stores only closed windows — anything with `end > now` is fetched fresh every time.
- Survives process restart. Same `cache_dir` between runs reuses entries.
- Does **not** version on `code_version`. If the server is upgraded with new feature logic, call `client.clear_cache()` after.

### Pandas-first surface

Already wedded to pandas? Reach the `.pandas` accessor on any client — every method returns `pandas.DataFrame` instead of Polars:

```python
with MuninnClient() as m:
    df = m.pandas.get_features(
        instrument="BTC-USDT",
        features=["vwap.1m", "obi"],
        start="2026-05-10T14:00:00Z",
        end="2026-05-10T15:00:00Z",
    )
    df.head()  # pandas.DataFrame
```

Available on both `MuninnClient.pandas` and `AsyncMuninnClient.pandas`. The conversion is pyarrow-free — no extra heavy deps to install.


### Async client

For cooperative-multitasking contexts (FastAPI handlers, async notebooks, integration with other async tooling), use the async sibling — same surface, same return types, `httpx.AsyncClient` underneath:

```python
from muninn import AsyncMuninnClient

async with AsyncMuninnClient() as m:
    df = await m.get_features(
        instrument="BTC-USDT",
        features=["vwap.1m", "obi", "vpin"],
        start="2026-05-10T14:00:00Z",
        end="2026-05-10T15:00:00Z",
    )
```

`AsyncMuninnClient.get_features` always fans out via `asyncio.gather`. The sync client uses a thread pool with the same effect — both eliminate the serial latency cost of multi-feature fetches.

### Live streaming (Server-Sent Events)

`get_feature` answers historical questions by reading the warehouse. To *watch* a feature evolve, attach to the server's live stream (`GET /api/v1/features/stream`, muninn [ADR-0009](https://github.com/lgreene03/muninn/blob/main/docs/adr/0009-streaming-features-sse.md)) and receive each value sub-second after its window closes:

```python
from muninn.streaming import MuninnStreamClient

with MuninnStreamClient() as stream:
    for event in stream.stream(feature="vwap.1m"):   # omit feature= for all features
        print(event.feature_name, event.value, event.window_end)
```

Async sibling — same surface, `async for`:

```python
from muninn.streaming import AsyncMuninnStreamClient

async with AsyncMuninnStreamClient() as stream:
    async for event in stream.stream(feature="vwap.1m"):
        ...
```

Each yielded object is a `FeatureValue`. The stream is a live tail with **no backfill** — for "last hour, then live", page `get_feature` for history and then attach the stream. From the shell: `muninn stream listen --feature vwap.1m` prints newline-delimited JSON.

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

## Releasing

Publishes go to PyPI via Trusted Publishing (OIDC) on tag push. See [docs/RELEASING.md](docs/RELEASING.md) for the one-time setup and the cut-a-release flow.

## Related

- [Muninn](https://github.com/lgreene03/muninn) — the server / platform this SDK targets.

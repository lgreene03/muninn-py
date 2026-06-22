# Getting Started

## Prerequisites

- Python 3.10 or newer
- A running [Muninn](https://github.com/lgreene03/muninn) server (the `query-api` service, default port 8080)

## Installation

```bash
pip install muninn-py
```

With notebook extras (JupyterLab, matplotlib, seaborn, pyarrow):

```bash
pip install "muninn-py[notebooks]"
```

With offline caching (`diskcache`):

```bash
pip install "muninn-py[cache]"
```

## Start a local Muninn server

The fastest way to get a server running locally is via Docker Compose. From the [muninn repo](https://github.com/lgreene03/muninn):

```bash
git clone https://github.com/lgreene03/muninn.git
cd muninn
docker compose up -d --wait
./scripts/smoke.sh   # confirms the Query API is healthy
```

The Query API listens on `http://localhost:8080` by default.

## First request

```python
from muninn import MuninnClient

with MuninnClient() as m:
    # Discover what features the server exposes
    for feat in m.list_features():
        print(feat.name, feat.version, feat.type)

    # Pull a multi-feature time-series panel
    df = m.get_features(
        instrument="BTC-USDT",
        features=["vwap.1m", "obi", "vpin"],
        start="2026-05-10T14:00:00Z",
        end="2026-05-10T15:00:00Z",
    )
    print(df.head())
```

## Custom server URL

```python
client = MuninnClient(host="http://my-server:8080")
```

## Async client

For pipelines that fan out many requests:

```python
import asyncio
from muninn import AsyncMuninnClient

async def main():
    async with AsyncMuninnClient() as m:
        df = await m.get_features(
            instrument="BTC-USDT",
            features=["vwap.1m", "obi"],
            start="2026-05-10T14:00:00Z",
            end="2026-05-10T15:00:00Z",
        )
        print(df.head())

asyncio.run(main())
```

## Replay jobs

```python
import time
from muninn import MuninnClient

with MuninnClient() as m:
    job = m.submit_replay_job(
        start="2026-05-10T14:00:00Z",
        end="2026-05-10T15:00:00Z",
        feature_version="v1",
    )
    print(f"submitted job_id={job.job_id} status={job.status}")

    while not job.is_terminal:
        time.sleep(2)
        job = m.get_replay_job(job.job_id)
        print(f"...status={job.status}")

    print(f"final status: {job.status}")
```

## Error handling

```python
from muninn import MuninnClient
from muninn.exceptions import MuninnNotFoundError, MuninnTimeoutError

with MuninnClient() as m:
    try:
        df = m.get_feature("unknown_feature", instrument="BTC-USDT",
                           start="2026-05-10T14:00:00Z", end="2026-05-10T15:00:00Z")
    except MuninnNotFoundError:
        print("Feature not registered on this server")
    except MuninnTimeoutError:
        print("Server took too long to respond")
```

## Offline caching

Install with `pip install "muninn-py[cache]"`, then:

```python
from muninn import MuninnClient

with MuninnClient(cache_dir=".muninn_cache") as m:
    # First call hits the server; subsequent calls with the same args
    # are served from disk.
    df = m.get_features(instrument="BTC-USDT", features=["vwap.1m"],
                        start="2026-05-10T14:00:00Z", end="2026-05-10T15:00:00Z")
```

## Next steps

- [Research diagnostics](api/research.md) — information coefficient, IC decay, signal half-life, and capacity analysis.
- [Factor model](api/factor.md) — Ledoit–Wolf-shrunk covariance and portfolio construction.
- [`examples/ic_capacity_research.py`](https://github.com/lgreene03/muninn-py/blob/main/examples/ic_capacity_research.py) wires the whole research pipeline together end to end on an offline synthetic panel.


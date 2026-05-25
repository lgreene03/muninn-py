# Notebooks

## alpha_backtest_demo.ipynb

[View on GitHub](https://github.com/lgreene03/muninn-py/blob/main/notebooks/alpha_backtest_demo.ipynb)

A self-contained research workflow demonstration against a running Muninn `query-api`.

**What it does:**

1. Connects to a local Muninn server and fetches `vwap.1m`, `obi`, and `vpin` for `BTC-USDT` over a 4-hour window.
2. Computes multi-period forward log-returns from the VWAP series using [`forward_returns`](api/notebook.md#muninn.notebook.forward_returns).
3. Measures the Information Coefficient (Spearman rank correlation between each signal and the 1-step-ahead return) using [`information_coefficient`](api/notebook.md#muninn.notebook.information_coefficient).
4. Plots a correlation heatmap and rolling IC using [`rolling_corr`](api/notebook.md#muninn.notebook.rolling_corr).
5. Submits a replay job and polls until completion, demonstrating the determinism property.

**Prerequisites:**

- A Muninn `query-api` server at `localhost:8080` with `vwap.1m`, `obi`, and `vpin` registered.
- `pip install "muninn-py[notebooks]"` for Polars, matplotlib, seaborn, and pyarrow.

**What this demo is not:** It is not a trading strategy. The point is reproducibility — every value produced by the notebook can be reproduced byte-for-byte by a server replay. See [DETERMINISTIC_REPLAY.md](https://github.com/lgreene03/muninn/blob/main/docs/steering/DETERMINISTIC_REPLAY.md) on the server side.

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
print(ic)
```

## feature_drift_monitoring.ipynb

[View on GitHub](https://github.com/lgreene03/muninn-py/blob/main/notebooks/feature_drift_monitoring.ipynb)

The SDK-side scaffolding a researcher would run interactively before wiring drift detection
into production monitoring.

**What it does:**

1. Pulls a single feature's recent history with [`get_feature`](api/client.md#muninn.client.MuninnClient.get_feature).
2. Splits the series into baseline and observed halves on `event_time`.
3. Computes drift metrics: Δmean in baseline σ, σ-ratio, p95 shift. Flags anything outside a
   researcher-defined band.
4. Visualises the two halves with a KDE overlay and a value-over-time plot marking the split.
5. Groups the panel by `code_version` to surface mid-window deploys — a class of regime change
   the value distribution alone won't explain.
6. Submits a replay over the same window with [`submit_replay_job`](api/client.md#muninn.client.MuninnClient.submit_replay_job)
   and reports `events_replayed`, elapsed time, and ms/event throughput as a sanity ratio.

**Prerequisites:**

- A Muninn `query-api` server at `localhost:8080` with at least one continuous-output feature
  registered (e.g. `vpin` or `obi`).
- `pip install "muninn-py[notebooks]"`.

**Editing the notebook.** The source lives in `notebooks/_build_drift_notebook.py` so diffs
read as Python instead of JSON. Regenerate the `.ipynb` after editing the source:

```bash
python notebooks/_build_drift_notebook.py
```

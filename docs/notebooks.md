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

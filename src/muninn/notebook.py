"""Notebook helper one-liners — the most common research operations against
a Muninn feature panel, kept as pure functions on Polars DataFrames.

.. note::
   ``method`` parameters on :func:`information_coefficient` are typed as
   ``Literal["pearson", "spearman"]`` to satisfy mypy's overload resolution
   against polars' typed signatures.

These functions are intentionally narrow. They are not a backtesting
framework, an alpha-modelling library, or a plotting helper. They are the
five-line snippets every quant repo eventually accumulates, given a name
and a test.

Conventions
-----------
- **Polars in, Polars out.** Pandas users convert at the boundary with
  ``.to_pandas()``.
- **No mutation.** Every function returns a new DataFrame.
- **No wall-clock reads.** Everything is a pure transformation of the
  inputs — matching the determinism discipline of the server.
- **Time-ordered.** All helpers assume input frames are sorted ascending
  by ``event_time`` (which every Muninn fetch returns sorted that way).

Example
-------
.. code-block:: python

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
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

import polars as pl

CorrMethod = Literal["pearson", "spearman"]


def forward_returns(
    df: pl.DataFrame,
    *,
    price_col: str,
    periods: int | Iterable[int] = 1,
    log: bool = True,
    suffix: str | None = None,
) -> pl.DataFrame:
    """Add forward-return columns derived from a price series.

    For each integer in ``periods``, adds a column named
    ``fwd_return_<n>`` (or ``<suffix>_<n>`` if ``suffix`` is provided)
    containing the n-step-ahead return.

    Parameters
    ----------
    df:
        Input frame. Must contain ``price_col``.
    price_col:
        Column to compute returns from — typically the Muninn VWAP
        feature, e.g. ``"vwap.1m"``.
    periods:
        Single int or iterable. The function shifts the price ``-n``
        rows to align the forward observation with the current row.
    log:
        When ``True`` (default), use ``log(p_t+n / p_t)``. When
        ``False``, use ``(p_t+n / p_t) - 1``.
    suffix:
        Override the default ``"fwd_return"`` column-name prefix.

    Returns
    -------
    pl.DataFrame
        Original frame plus the new forward-return columns. The last
        ``max(periods)`` rows have nulls for those columns because
        their forward observation is past the frame's end.
    """
    if price_col not in df.columns:
        raise KeyError(f"{price_col!r} not in DataFrame; have {df.columns}")

    if isinstance(periods, int):
        period_list = [periods]
    else:
        period_list = list(periods)
        if not period_list:
            raise ValueError("at least one period is required")

    prefix = suffix or "fwd_return"
    expressions: list[pl.Expr] = []
    base = pl.col(price_col).cast(pl.Float64)
    for n in period_list:
        if n <= 0:
            raise ValueError(f"period must be positive; got {n}")
        future = base.shift(-n)
        expr = (future / base).log() if log else (future / base) - 1
        expressions.append(expr.alias(f"{prefix}_{n}"))

    return df.with_columns(expressions)


def information_coefficient(
    df: pl.DataFrame,
    *,
    signals: Iterable[str],
    return_col: str,
    method: CorrMethod = "spearman",
) -> pl.DataFrame:
    """Compute the Spearman (default) or Pearson correlation between each
    signal column and a forward-return column.

    A standard "Information Coefficient" for alpha research — one number
    per signal showing rank correlation with the target. Toy by design;
    real research adds rolling windows, winsorization, and significance
    tests on top.

    Returns
    -------
    pl.DataFrame
        Two columns: ``signal`` (the column name) and ``ic`` (the
        correlation). Rows with any null in either column are dropped
        before correlation.
    """
    # ``method`` is constrained at type level, but a runtime check guards
    # against callers passing an untyped string from CLI / JSON paths.
    if method not in ("spearman", "pearson"):
        raise ValueError("method must be 'spearman' or 'pearson'")
    if return_col not in df.columns:
        raise KeyError(f"{return_col!r} not in DataFrame")

    rows: list[dict[str, object]] = []
    for sig in signals:
        if sig not in df.columns:
            raise KeyError(f"{sig!r} not in DataFrame")

        clean = df.select([sig, return_col]).drop_nulls()
        if clean.is_empty():
            rows.append({"signal": sig, "ic": None})
            continue

        # pl.corr supports method="pearson"|"spearman" directly.
        ic_value = clean.select(
            pl.corr(
                pl.col(sig).cast(pl.Float64),
                pl.col(return_col).cast(pl.Float64),
                method=method,
            ).alias("ic")
        ).item(0, "ic")
        rows.append({"signal": sig, "ic": ic_value})

    return pl.DataFrame(rows).sort("ic", descending=True, nulls_last=True)


def rolling_corr(
    df: pl.DataFrame,
    *,
    a: str,
    b: str,
    window: int,
    min_periods: int | None = None,
) -> pl.DataFrame:
    """Add a rolling Pearson correlation column between two existing columns.

    Useful for spotting regime changes in signal-return relationships.

    Returns
    -------
    pl.DataFrame
        Original frame plus a column ``rolling_corr_<a>_<b>`` (or
        ``rolling_corr`` if both names produce a too-long header).
    """
    if a not in df.columns or b not in df.columns:
        raise KeyError(f"{a!r} and {b!r} must both be in the DataFrame")
    if window <= 1:
        raise ValueError("window must be > 1")

    col_name = f"rolling_corr_{a}_{b}"[:64]  # keep within typical readability
    expr = pl.rolling_corr(
        pl.col(a).cast(pl.Float64),
        pl.col(b).cast(pl.Float64),
        window_size=window,
        min_samples=min_periods if min_periods is not None else window,
    ).alias(col_name)
    return df.with_columns(expr)


def hit_rate(
    df: pl.DataFrame,
    *,
    signal: str,
    return_col: str,
    threshold: float = 0.0,
) -> float:
    """Fraction of (signal > threshold) rows where ``return_col`` is also
    positive. A trivial "directional agreement" measure.

    Returns ``nan`` when there are no rows above the threshold.
    """
    if signal not in df.columns or return_col not in df.columns:
        raise KeyError(f"both {signal!r} and {return_col!r} must be in the DataFrame")

    above = df.filter(pl.col(signal).cast(pl.Float64) > threshold).drop_nulls(
        subset=[return_col]
    )
    if above.is_empty():
        return float("nan")
    hits = above.filter(pl.col(return_col).cast(pl.Float64) > 0).height
    return hits / above.height

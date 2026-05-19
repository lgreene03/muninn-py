"""Tests for the ``muninn.notebook`` helper functions."""

from __future__ import annotations

import math

import polars as pl
import pytest

from muninn.notebook import (
    forward_returns,
    hit_rate,
    information_coefficient,
    rolling_corr,
)

# ----- forward_returns ------------------------------------------------------


def test_forward_returns_log_default() -> None:
    df = pl.DataFrame(
        {
            "event_time": pl.datetime_range(
                pl.datetime(2026, 5, 10, 14, 0), pl.datetime(2026, 5, 10, 14, 4),
                interval="1m", eager=True
            ),
            "vwap.1m": [100.0, 110.0, 121.0, 133.1, 146.41],
        }
    )

    out = forward_returns(df, price_col="vwap.1m", periods=1, log=True)

    assert "fwd_return_1" in out.columns
    # log(110/100) ≈ 0.0953
    assert out["fwd_return_1"][0] == pytest.approx(math.log(110 / 100), rel=1e-6)
    # Last row has no forward observation.
    assert out["fwd_return_1"][-1] is None


def test_forward_returns_simple_returns() -> None:
    df = pl.DataFrame({"p": [100.0, 110.0, 121.0]})
    out = forward_returns(df, price_col="p", periods=1, log=False)
    # (110/100) - 1 == 0.10
    assert out["fwd_return_1"][0] == pytest.approx(0.10, rel=1e-9)


def test_forward_returns_multi_period() -> None:
    df = pl.DataFrame({"p": [100.0, 110.0, 121.0, 133.1, 146.41]})
    out = forward_returns(df, price_col="p", periods=[1, 3], log=True)
    assert {"fwd_return_1", "fwd_return_3"} <= set(out.columns)


def test_forward_returns_custom_suffix() -> None:
    df = pl.DataFrame({"p": [1.0, 2.0]})
    out = forward_returns(df, price_col="p", periods=1, suffix="ret")
    assert "ret_1" in out.columns


def test_forward_returns_validates_price_col() -> None:
    df = pl.DataFrame({"x": [1.0]})
    with pytest.raises(KeyError):
        forward_returns(df, price_col="missing", periods=1)


def test_forward_returns_validates_periods() -> None:
    df = pl.DataFrame({"p": [1.0]})
    with pytest.raises(ValueError, match="positive"):
        forward_returns(df, price_col="p", periods=0)
    with pytest.raises(ValueError, match="at least one"):
        forward_returns(df, price_col="p", periods=[])


# ----- information_coefficient ---------------------------------------------


def test_information_coefficient_perfectly_correlated_signal_ranks_first() -> None:
    df = pl.DataFrame(
        {
            "signal_good": [1.0, 2.0, 3.0, 4.0, 5.0],
            "signal_noise": [3.0, 1.0, 4.0, 1.0, 5.0],
            "fwd_return_1": [0.1, 0.2, 0.3, 0.4, 0.5],
        }
    )

    out = information_coefficient(
        df, signals=["signal_good", "signal_noise"], return_col="fwd_return_1"
    )

    assert out["signal"][0] == "signal_good"
    assert out["ic"][0] == pytest.approx(1.0, abs=1e-9)


def test_information_coefficient_pearson_method() -> None:
    df = pl.DataFrame(
        {
            "x": [1.0, 2.0, 3.0, 4.0, 5.0],
            "y": [2.0, 4.0, 6.0, 8.0, 10.0],
        }
    )
    out = information_coefficient(df, signals=["x"], return_col="y", method="pearson")
    assert out["ic"][0] == pytest.approx(1.0, abs=1e-9)


def test_information_coefficient_validates_signal() -> None:
    df = pl.DataFrame({"x": [1.0], "fwd": [1.0]})
    with pytest.raises(KeyError):
        information_coefficient(df, signals=["missing"], return_col="fwd")


def test_information_coefficient_validates_method() -> None:
    """Runtime guard against an untyped string passed from CLI / JSON paths."""
    df = pl.DataFrame({"x": [1.0], "fwd": [1.0]})
    with pytest.raises(ValueError, match="spearman"):
        information_coefficient(
            df,
            signals=["x"],
            return_col="fwd",
            method="kendall",  # type: ignore[arg-type]
        )


def test_information_coefficient_drops_nulls_per_signal() -> None:
    df = pl.DataFrame(
        {
            "sig": [1.0, 2.0, None, 4.0, 5.0],
            "fwd": [0.1, 0.2, 0.3, 0.4, None],
        }
    )
    out = information_coefficient(df, signals=["sig"], return_col="fwd")
    assert out["ic"][0] is not None


# ----- rolling_corr ---------------------------------------------------------


def test_rolling_corr_adds_named_column() -> None:
    df = pl.DataFrame({"a": [1.0, 2, 3, 4, 5, 6, 7, 8], "b": [2.0, 4, 6, 8, 10, 12, 14, 16]})
    out = rolling_corr(df, a="a", b="b", window=3)
    assert any(c.startswith("rolling_corr_") for c in out.columns)


def test_rolling_corr_validates_window() -> None:
    df = pl.DataFrame({"a": [1.0], "b": [1.0]})
    with pytest.raises(ValueError, match="window"):
        rolling_corr(df, a="a", b="b", window=1)


# ----- hit_rate -------------------------------------------------------------


def test_hit_rate_directional_agreement() -> None:
    df = pl.DataFrame(
        {
            "sig": [1.0, 1.0, 1.0, -1.0],
            "fwd": [0.1, -0.1, 0.2, -0.3],
        }
    )
    # 3 rows above threshold 0, 2 of which have positive return.
    assert hit_rate(df, signal="sig", return_col="fwd") == pytest.approx(2 / 3)


def test_hit_rate_no_rows_above_threshold_returns_nan() -> None:
    df = pl.DataFrame({"sig": [-1.0, -2.0], "fwd": [1.0, 1.0]})
    assert math.isnan(hit_rate(df, signal="sig", return_col="fwd"))

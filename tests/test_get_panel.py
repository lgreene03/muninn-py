"""Tests for ``get_panel`` on both sync and async clients."""

from __future__ import annotations

import httpx
import polars as pl
import pytest
import respx

from muninn import AsyncMuninnClient, MuninnClient

BASE_URL = "http://muninn.test"


def _feature_value(event_time: str, value: str, name: str) -> dict[str, object]:
    return {
        "eventId": "019e1e50-7979-7000-9ccc-e4e309080a2c",
        "eventTime": event_time,
        "featureName": name,
        "featureVersion": "v1",
        "value": value,
        "windowStart": event_time,
        "windowEnd": event_time,
        "inputEventIds": [],
        "codeVersion": "dev",
    }


def _mock_feature(name: str, rows: list[dict[str, object]]) -> None:
    """Register a respx route for one feature that returns the given rows for any instrument."""
    respx.get(f"{BASE_URL}/api/v1/features/{name}").mock(
        return_value=httpx.Response(200, json=rows)
    )


# ----- sync -----------------------------------------------------------------


@respx.mock
def test_sync_get_panel_returns_long_form_frame() -> None:
    _mock_feature(
        "vwap.1m",
        [
            _feature_value("2026-05-10T14:00:00Z", "60000", name="vwap.1m"),
            _feature_value("2026-05-10T14:01:00Z", "60010", name="vwap.1m"),
        ],
    )
    _mock_feature(
        "obi",
        [
            _feature_value("2026-05-10T14:00:00Z", "0.4", name="obi"),
            _feature_value("2026-05-10T14:01:00Z", "0.5", name="obi"),
        ],
    )

    with MuninnClient(host=BASE_URL) as client:
        df = client.get_panel(
            instruments=["BTC-USDT", "ETH-USDT"],
            features=["vwap.1m", "obi"],
            start="2026-05-10T14:00:00Z",
            end="2026-05-10T15:00:00Z",
        )

    assert isinstance(df, pl.DataFrame)
    assert set(df.columns) == {"instrument", "event_time", "vwap.1m", "obi"}
    # Two instruments × two timestamps = four rows.
    assert df.height == 4
    assert sorted(df["instrument"].unique().to_list()) == ["BTC-USDT", "ETH-USDT"]


@respx.mock
def test_sync_get_panel_sorts_by_instrument_then_time() -> None:
    _mock_feature(
        "vwap.1m",
        [
            _feature_value("2026-05-10T14:01:00Z", "1", name="vwap.1m"),
            _feature_value("2026-05-10T14:00:00Z", "2", name="vwap.1m"),
        ],
    )

    with MuninnClient(host=BASE_URL) as client:
        df = client.get_panel(
            instruments=["BTC-USDT", "ETH-USDT"],
            features=["vwap.1m"],
            start="2026-05-10T14:00:00Z",
            end="2026-05-10T15:00:00Z",
        )

    # Within each instrument: event_time ascending.
    btc_times = df.filter(pl.col("instrument") == "BTC-USDT")["event_time"].to_list()
    assert btc_times == sorted(btc_times)


@respx.mock
def test_sync_get_panel_empty_when_no_rows_anywhere() -> None:
    _mock_feature("vwap.1m", [])

    with MuninnClient(host=BASE_URL) as client:
        df = client.get_panel(
            instruments=["BTC-USDT", "ETH-USDT"],
            features=["vwap.1m"],
            start="2026-05-10T14:00:00Z",
            end="2026-05-10T15:00:00Z",
        )

    assert df.is_empty()
    # Schema is preserved.
    assert "instrument" in df.columns
    assert "event_time" in df.columns


def test_sync_get_panel_validates_inputs() -> None:
    with MuninnClient(host=BASE_URL) as client:
        with pytest.raises(ValueError, match="instrument"):
            client.get_panel(
                instruments=[],
                features=["vwap.1m"],
                start="2026-05-10T14:00:00Z",
                end="2026-05-10T15:00:00Z",
            )
        with pytest.raises(ValueError, match="feature"):
            client.get_panel(
                instruments=["BTC-USDT"],
                features=[],
                start="2026-05-10T14:00:00Z",
                end="2026-05-10T15:00:00Z",
            )


# ----- async ----------------------------------------------------------------


@respx.mock
async def test_async_get_panel_matches_sync_shape() -> None:
    _mock_feature(
        "vwap.1m",
        [
            _feature_value("2026-05-10T14:00:00Z", "60000", name="vwap.1m"),
        ],
    )

    async with AsyncMuninnClient(host=BASE_URL) as client:
        df = await client.get_panel(
            instruments=["BTC-USDT", "ETH-USDT"],
            features=["vwap.1m"],
            start="2026-05-10T14:00:00Z",
            end="2026-05-10T15:00:00Z",
        )

    assert set(df.columns) == {"instrument", "event_time", "vwap.1m"}
    assert df.height == 2
    assert sorted(df["instrument"].unique().to_list()) == ["BTC-USDT", "ETH-USDT"]


async def test_async_get_panel_validates_inputs() -> None:
    async with AsyncMuninnClient(host=BASE_URL) as client:
        with pytest.raises(ValueError, match="instrument"):
            await client.get_panel(
                instruments=[],
                features=["vwap.1m"],
                start="2026-05-10T14:00:00Z",
                end="2026-05-10T15:00:00Z",
            )

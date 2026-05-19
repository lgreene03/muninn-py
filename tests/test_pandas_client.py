"""Tests for the ``.pandas`` accessor on both sync and async clients."""

from __future__ import annotations

import httpx
import pandas as pd
import respx

from muninn import AsyncMuninnClient, MuninnClient

BASE_URL = "http://muninn.test"


def _feature_value(event_time: str, value: str, name: str = "vwap.1m") -> dict[str, object]:
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


# ----- sync .pandas accessor ------------------------------------------------


@respx.mock
def test_sync_pandas_get_feature_returns_pandas_frame() -> None:
    respx.get(f"{BASE_URL}/api/v1/features/vwap.1m").mock(
        return_value=httpx.Response(
            200,
            json=[_feature_value("2026-05-10T14:00:00Z", "60000")],
        )
    )

    with MuninnClient(host=BASE_URL) as client:
        df = client.pandas.get_feature(
            "vwap.1m",
            instrument="BTC-USDT",
            start="2026-05-10T14:00:00Z",
            end="2026-05-10T15:00:00Z",
        )

    assert isinstance(df, pd.DataFrame)
    assert "event_time" in df.columns
    assert df.iloc[0]["value"] == 60000.0


@respx.mock
def test_sync_pandas_get_features_returns_pandas_frame() -> None:
    respx.get(f"{BASE_URL}/api/v1/features/vwap.1m").mock(
        return_value=httpx.Response(200, json=[_feature_value("2026-05-10T14:00:00Z", "60000")])
    )
    respx.get(f"{BASE_URL}/api/v1/features/obi").mock(
        return_value=httpx.Response(
            200, json=[_feature_value("2026-05-10T14:00:00Z", "0.4", name="obi")]
        )
    )

    with MuninnClient(host=BASE_URL) as client:
        df = client.pandas.get_features(
            instrument="BTC-USDT",
            features=["vwap.1m", "obi"],
            start="2026-05-10T14:00:00Z",
            end="2026-05-10T15:00:00Z",
        )

    assert isinstance(df, pd.DataFrame)
    assert {"event_time", "vwap.1m", "obi"} <= set(df.columns)


@respx.mock
def test_sync_pandas_get_panel_returns_pandas_frame() -> None:
    respx.get(f"{BASE_URL}/api/v1/features/vwap.1m").mock(
        return_value=httpx.Response(200, json=[_feature_value("2026-05-10T14:00:00Z", "60000")])
    )

    with MuninnClient(host=BASE_URL) as client:
        df = client.pandas.get_panel(
            instruments=["BTC-USDT", "ETH-USDT"],
            features=["vwap.1m"],
            start="2026-05-10T14:00:00Z",
            end="2026-05-10T15:00:00Z",
        )

    assert isinstance(df, pd.DataFrame)
    assert "instrument" in df.columns
    assert df["instrument"].nunique() == 2


def test_sync_pandas_accessor_is_lazy_and_cached() -> None:
    with MuninnClient(host=BASE_URL) as client:
        accessor1 = client.pandas
        accessor2 = client.pandas
    # Same instance returned on repeated access (no re-import on every call).
    assert accessor1 is accessor2


# ----- async .pandas accessor ----------------------------------------------


@respx.mock
async def test_async_pandas_get_feature_returns_pandas_frame() -> None:
    respx.get(f"{BASE_URL}/api/v1/features/vwap.1m").mock(
        return_value=httpx.Response(
            200,
            json=[_feature_value("2026-05-10T14:00:00Z", "60000")],
        )
    )

    async with AsyncMuninnClient(host=BASE_URL) as client:
        df = await client.pandas.get_feature(
            "vwap.1m",
            instrument="BTC-USDT",
            start="2026-05-10T14:00:00Z",
            end="2026-05-10T15:00:00Z",
        )

    assert isinstance(df, pd.DataFrame)
    assert df.iloc[0]["value"] == 60000.0


@respx.mock
async def test_async_pandas_get_features_returns_pandas_frame() -> None:
    respx.get(f"{BASE_URL}/api/v1/features/vwap.1m").mock(
        return_value=httpx.Response(200, json=[_feature_value("2026-05-10T14:00:00Z", "60000")])
    )
    respx.get(f"{BASE_URL}/api/v1/features/obi").mock(
        return_value=httpx.Response(
            200, json=[_feature_value("2026-05-10T14:00:00Z", "0.4", name="obi")]
        )
    )

    async with AsyncMuninnClient(host=BASE_URL) as client:
        df = await client.pandas.get_features(
            instrument="BTC-USDT",
            features=["vwap.1m", "obi"],
            start="2026-05-10T14:00:00Z",
            end="2026-05-10T15:00:00Z",
        )

    assert isinstance(df, pd.DataFrame)
    assert {"event_time", "vwap.1m", "obi"} <= set(df.columns)

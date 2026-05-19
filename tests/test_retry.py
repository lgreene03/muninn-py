"""Tests for retry on transient failures across both clients."""

from __future__ import annotations

import httpx
import pytest
import respx

from muninn import AsyncMuninnClient, MuninnClient, RetryConfig
from muninn.exceptions import MuninnAPIError, MuninnNotFoundError

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


# Zero-backoff config so tests are fast and deterministic.
_FAST = RetryConfig(max_attempts=3, initial_backoff=0.0, max_backoff=0.0, jitter=0.0)


# ----- retry config --------------------------------------------------------


def test_retry_config_rejects_invalid_values() -> None:
    with pytest.raises(ValueError):
        RetryConfig(max_attempts=0)
    with pytest.raises(ValueError):
        RetryConfig(initial_backoff=-1)
    with pytest.raises(ValueError):
        RetryConfig(backoff_factor=0)


def test_retry_config_delay_is_capped() -> None:
    cfg = RetryConfig(initial_backoff=1.0, max_backoff=4.0, backoff_factor=2.0, jitter=0.0)
    # 1, 2, 4, 4, 4, ...
    assert cfg.delay_for_attempt(1) == 1.0
    assert cfg.delay_for_attempt(2) == 2.0
    assert cfg.delay_for_attempt(3) == 4.0
    assert cfg.delay_for_attempt(4) == 4.0


def test_retry_config_disabled_helper() -> None:
    assert RetryConfig(max_attempts=1).disabled() is True
    assert RetryConfig(max_attempts=3).disabled() is False


# ----- sync retry behavior --------------------------------------------------


@respx.mock
def test_sync_retries_on_503_then_succeeds() -> None:
    route = respx.get(f"{BASE_URL}/api/v1/features/vwap.1m").mock(
        side_effect=[
            httpx.Response(503, json={"message": "warming up"}),
            httpx.Response(503, json={"message": "still warming up"}),
            httpx.Response(200, json=[_feature_value("2026-05-10T14:00:00Z", "60000")]),
        ]
    )

    with MuninnClient(host=BASE_URL, retry=_FAST) as client:
        df = client.get_feature(
            "vwap.1m",
            instrument="BTC-USDT",
            start="2026-05-10T14:00:00Z",
            end="2026-05-10T15:00:00Z",
        )

    assert df.height == 1
    assert route.call_count == 3


@respx.mock
def test_sync_returns_final_5xx_after_max_attempts() -> None:
    respx.get(f"{BASE_URL}/api/v1/features/vwap.1m").mock(
        return_value=httpx.Response(503, json={"message": "down"})
    )

    with MuninnClient(host=BASE_URL, retry=_FAST) as client, pytest.raises(MuninnAPIError) as exc:
        client.get_feature(
            "vwap.1m",
            instrument="BTC-USDT",
            start="2026-05-10T14:00:00Z",
            end="2026-05-10T15:00:00Z",
        )

    assert exc.value.status_code == 503


@respx.mock
def test_sync_does_not_retry_404() -> None:
    route = respx.get(f"{BASE_URL}/api/v1/features/missing").mock(
        return_value=httpx.Response(404, json={"message": "no such feature"})
    )

    with MuninnClient(host=BASE_URL, retry=_FAST) as client, pytest.raises(MuninnNotFoundError):
        client.get_feature(
            "missing",
            instrument="BTC-USDT",
            start="2026-05-10T14:00:00Z",
            end="2026-05-10T15:00:00Z",
        )

    # 4xx is not retryable — only one call.
    assert route.call_count == 1


@respx.mock
def test_sync_retries_on_connection_error_then_succeeds() -> None:
    route = respx.get(f"{BASE_URL}/api/v1/features/vwap.1m").mock(
        side_effect=[
            httpx.ConnectError("network unreachable"),
            httpx.Response(200, json=[_feature_value("2026-05-10T14:00:00Z", "60000")]),
        ]
    )

    with MuninnClient(host=BASE_URL, retry=_FAST) as client:
        df = client.get_feature(
            "vwap.1m",
            instrument="BTC-USDT",
            start="2026-05-10T14:00:00Z",
            end="2026-05-10T15:00:00Z",
        )

    assert df.height == 1
    assert route.call_count == 2


@respx.mock
def test_sync_retry_disabled_when_max_attempts_one() -> None:
    route = respx.get(f"{BASE_URL}/api/v1/features/vwap.1m").mock(
        return_value=httpx.Response(503, json={"message": "down"})
    )

    with (
        MuninnClient(host=BASE_URL, retry=RetryConfig(max_attempts=1)) as client,
        pytest.raises(MuninnAPIError),
    ):
        client.get_feature(
            "vwap.1m",
            instrument="BTC-USDT",
            start="2026-05-10T14:00:00Z",
            end="2026-05-10T15:00:00Z",
        )

    assert route.call_count == 1


# ----- async retry behavior -------------------------------------------------


@respx.mock
async def test_async_retries_on_503_then_succeeds() -> None:
    route = respx.get(f"{BASE_URL}/api/v1/features/vwap.1m").mock(
        side_effect=[
            httpx.Response(502, json={}),
            httpx.Response(200, json=[_feature_value("2026-05-10T14:00:00Z", "60000")]),
        ]
    )

    async with AsyncMuninnClient(host=BASE_URL, retry=_FAST) as client:
        df = await client.get_feature(
            "vwap.1m",
            instrument="BTC-USDT",
            start="2026-05-10T14:00:00Z",
            end="2026-05-10T15:00:00Z",
        )

    assert df.height == 1
    assert route.call_count == 2


@respx.mock
async def test_async_retries_on_connection_error() -> None:
    route = respx.get(f"{BASE_URL}/api/v1/features/vwap.1m").mock(
        side_effect=[
            httpx.ReadError("connection reset"),
            httpx.Response(200, json=[]),
        ]
    )

    async with AsyncMuninnClient(host=BASE_URL, retry=_FAST) as client:
        df = await client.get_feature(
            "vwap.1m",
            instrument="BTC-USDT",
            start="2026-05-10T14:00:00Z",
            end="2026-05-10T15:00:00Z",
        )

    assert df.is_empty()
    assert route.call_count == 2


# ----- pool tunables --------------------------------------------------------


def test_pool_limits_passed_to_httpx() -> None:
    """Constructing the client with pool tunables shouldn't blow up.

    The actual pool behavior is httpx-internal; we just verify the
    constructor accepts the params and the client is usable.
    """
    with MuninnClient(
        host=BASE_URL,
        max_connections=50,
        max_keepalive_connections=10,
        keepalive_expiry=2.0,
    ) as client:
        # If httpx rejected our limits, the client constructor would have raised.
        assert client._client is not None  # noqa: SLF001


def test_timeout_accepts_httpx_timeout_object() -> None:
    """Pass-through of the httpx.Timeout object for per-op timeouts."""
    timeout = httpx.Timeout(connect=1.0, read=5.0, write=2.0, pool=3.0)
    with MuninnClient(host=BASE_URL, timeout=timeout) as client:
        assert client._client.timeout.connect == 1.0  # noqa: SLF001
        assert client._client.timeout.read == 5.0  # noqa: SLF001

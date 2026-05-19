"""Tests for the optional disk-based response cache."""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone

import httpx
import respx

from muninn import AsyncMuninnClient, MuninnClient
from muninn._cache import cache_key, is_cacheable

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


# ----- cache-key / cacheability helpers -------------------------------------


def test_cache_key_is_deterministic() -> None:
    args: dict[str, object] = {
        "host": "http://h",
        "feature": "vwap.1m",
        "instrument": "BTC-USDT",
        "start": "2026-01-01T00:00:00Z",
        "end": "2026-01-01T01:00:00Z",
        "limit": None,
    }
    assert cache_key(**args) == cache_key(**args)


def test_cache_key_varies_with_limit() -> None:
    base: dict[str, object] = {
        "host": "http://h",
        "feature": "vwap.1m",
        "instrument": "BTC-USDT",
        "start": "2026-01-01T00:00:00Z",
        "end": "2026-01-01T01:00:00Z",
    }
    assert cache_key(**base, limit=None) != cache_key(**base, limit=100)


def test_cache_key_varies_with_feature_and_instrument() -> None:
    base: dict[str, object] = {
        "host": "http://h",
        "start": "2026-01-01T00:00:00Z",
        "end": "2026-01-01T01:00:00Z",
        "limit": None,
    }
    k1 = cache_key(**base, feature="vwap.1m", instrument="BTC-USDT")
    k2 = cache_key(**base, feature="obi", instrument="BTC-USDT")
    k3 = cache_key(**base, feature="vwap.1m", instrument="ETH-USDT")
    assert k1 != k2
    assert k1 != k3


def test_is_cacheable_past_window() -> None:
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert is_cacheable(past) is True


def test_is_cacheable_future_window_rejected() -> None:
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    assert is_cacheable(future) is False


# ----- sync caching ---------------------------------------------------------


@respx.mock
def test_sync_cache_hit_avoids_second_http_call() -> None:
    past_start = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    past_end = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat().replace("+00:00", "Z")

    route = respx.get(f"{BASE_URL}/api/v1/features/vwap.1m").mock(
        return_value=httpx.Response(
            200, json=[_feature_value("2026-05-10T14:00:00Z", "60000")]
        )
    )

    with tempfile.TemporaryDirectory() as tmp, MuninnClient(host=BASE_URL, cache_dir=tmp) as client:
        df1 = client.get_feature(
            "vwap.1m", instrument="BTC-USDT", start=past_start, end=past_end
        )
        df2 = client.get_feature(
            "vwap.1m", instrument="BTC-USDT", start=past_start, end=past_end
        )

    assert df1.height == df2.height == 1
    # Two SDK calls, but only one HTTP call.
    assert route.call_count == 1


@respx.mock
def test_sync_cache_skipped_for_future_windows() -> None:
    """A range whose end is in the future is never cached.

    The server may still be receiving events in that range — the answer
    could change on the next call.
    """
    past_start = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    future_end = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")

    route = respx.get(f"{BASE_URL}/api/v1/features/vwap.1m").mock(
        return_value=httpx.Response(200, json=[_feature_value("2026-05-10T14:00:00Z", "1")])
    )

    with tempfile.TemporaryDirectory() as tmp, MuninnClient(host=BASE_URL, cache_dir=tmp) as client:
        client.get_feature(
            "vwap.1m", instrument="BTC-USDT", start=past_start, end=future_end
        )
        client.get_feature(
            "vwap.1m", instrument="BTC-USDT", start=past_start, end=future_end
        )

    # Two HTTP calls — cache refused to store the open window.
    assert route.call_count == 2


@respx.mock
def test_sync_clear_cache_drops_entries_and_forces_refetch() -> None:
    past_start = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    past_end = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat().replace("+00:00", "Z")

    route = respx.get(f"{BASE_URL}/api/v1/features/vwap.1m").mock(
        return_value=httpx.Response(200, json=[_feature_value("2026-05-10T14:00:00Z", "1")])
    )

    with tempfile.TemporaryDirectory() as tmp, MuninnClient(host=BASE_URL, cache_dir=tmp) as client:
        client.get_feature(
            "vwap.1m", instrument="BTC-USDT", start=past_start, end=past_end
        )
        assert route.call_count == 1

        removed = client.clear_cache()
        assert removed >= 1

        client.get_feature(
            "vwap.1m", instrument="BTC-USDT", start=past_start, end=past_end
        )
        assert route.call_count == 2


def test_sync_clear_cache_when_no_cache_configured() -> None:
    """No-op without raising when cache_dir wasn't supplied."""
    with MuninnClient(host=BASE_URL) as client:
        assert client.clear_cache() == 0


def test_sync_cache_persists_across_clients_on_same_dir() -> None:
    """Cache contents survive a process restart, demonstrated by reopening."""
    past_start = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    past_end = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat().replace("+00:00", "Z")

    with tempfile.TemporaryDirectory() as tmp:
        # First "process": one HTTP call, populates cache.
        with respx.mock:
            route1 = respx.get(f"{BASE_URL}/api/v1/features/vwap.1m").mock(
                return_value=httpx.Response(
                    200, json=[_feature_value("2026-05-10T14:00:00Z", "1")]
                )
            )
            with MuninnClient(host=BASE_URL, cache_dir=tmp) as c:
                c.get_feature(
                    "vwap.1m", instrument="BTC-USDT", start=past_start, end=past_end
                )
                assert route1.call_count == 1

        # Second "process": same dir, no HTTP allowed — must hit cache.
        with respx.mock:
            route2 = respx.get(f"{BASE_URL}/api/v1/features/vwap.1m").mock(
                return_value=httpx.Response(500, text="should not be called")
            )
            with MuninnClient(host=BASE_URL, cache_dir=tmp) as c:
                df = c.get_feature(
                    "vwap.1m", instrument="BTC-USDT", start=past_start, end=past_end
                )

        assert df.height == 1
        assert route2.call_count == 0


# ----- async caching --------------------------------------------------------


@respx.mock
async def test_async_cache_hit_avoids_second_http_call() -> None:
    past_start = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    past_end = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat().replace("+00:00", "Z")

    route = respx.get(f"{BASE_URL}/api/v1/features/vwap.1m").mock(
        return_value=httpx.Response(200, json=[_feature_value("2026-05-10T14:00:00Z", "1")])
    )

    with tempfile.TemporaryDirectory() as tmp:  # noqa: SIM117
        async with AsyncMuninnClient(host=BASE_URL, cache_dir=tmp) as client:
            await client.get_feature(
                "vwap.1m", instrument="BTC-USDT", start=past_start, end=past_end
            )
            await client.get_feature(
                "vwap.1m", instrument="BTC-USDT", start=past_start, end=past_end
            )

        assert route.call_count == 1

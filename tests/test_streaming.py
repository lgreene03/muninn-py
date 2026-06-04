"""Unit tests for the live feature streaming clients (SSE).

Mocks the ``text/event-stream`` response with respx and asserts both the sync
and async clients parse SSE frames into ``FeatureValue`` objects, honor the
``feature`` filter, skip keepalives/non-feature events, and map HTTP errors.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from muninn import AsyncMuninnStreamClient, MuninnStreamClient
from muninn.exceptions import MuninnNotFoundError, MuninnStreamError
from muninn.models import FeatureValue

BASE_URL = "http://muninn.test"
STREAM_URL = f"{BASE_URL}/api/v1/features/stream"


def _frame(event_time: str, value: str, name: str = "vwap.1m") -> str:
    data = (
        "{"
        f'"eventId": "019e1e50-7979-7000-9ccc-e4e309080a2c",'
        f'"eventTime": "{event_time}",'
        f'"featureName": "{name}",'
        f'"featureVersion": "v1",'
        f'"value": {value},'
        f'"windowStart": "{event_time}",'
        f'"windowEnd": "{event_time}",'
        f'"inputEventIds": [],'
        f'"codeVersion": "dev"'
        "}"
    )
    return f"event: feature\ndata: {data}\n\n"


# A realistic stream body: a keepalive comment, two feature events, and a
# non-feature event that must be ignored.
SSE_BODY = (
    ":keepalive\n\n"
    + _frame("2026-05-10T14:00:00Z", "60000.00")
    + _frame("2026-05-10T14:01:00Z", "60005.00")
    + "event: ping\ndata: {}\n\n"
).encode()

SSE_HEADERS = {"content-type": "text/event-stream"}


def _mock_stream() -> None:
    respx.get(STREAM_URL).mock(
        return_value=httpx.Response(200, content=SSE_BODY, headers=SSE_HEADERS)
    )


@respx.mock
def test_sync_stream_parses_feature_events() -> None:
    _mock_stream()

    with MuninnStreamClient(host=BASE_URL) as client:
        events = list(client.stream())

    assert all(isinstance(e, FeatureValue) for e in events)
    assert [e.feature_name for e in events] == ["vwap.1m", "vwap.1m"]
    assert [float(e.value) for e in events if e.value is not None] == [60000.0, 60005.0]
    assert events[0].window_end is not None


@respx.mock
def test_sync_stream_passes_feature_filter() -> None:
    route = respx.get(STREAM_URL).mock(
        return_value=httpx.Response(200, content=SSE_BODY, headers=SSE_HEADERS)
    )

    with MuninnStreamClient(host=BASE_URL) as client:
        list(client.stream(feature="vwap.1m"))

    assert route.called
    assert route.calls.last.request.url.params["feature"] == "vwap.1m"


@respx.mock
def test_sync_stream_maps_http_error() -> None:
    respx.get(STREAM_URL).mock(
        return_value=httpx.Response(404, json={"message": "no such feature"})
    )

    with MuninnStreamClient(host=BASE_URL) as client, pytest.raises(MuninnNotFoundError):
        list(client.stream(feature="nope"))


@respx.mock
def test_sync_stream_raises_on_malformed_frame() -> None:
    body = b"event: feature\ndata: {not json}\n\n"
    respx.get(STREAM_URL).mock(
        return_value=httpx.Response(200, content=body, headers=SSE_HEADERS)
    )

    with MuninnStreamClient(host=BASE_URL) as client, pytest.raises(MuninnStreamError):
        list(client.stream())


@respx.mock
async def test_async_stream_parses_feature_events() -> None:
    _mock_stream()

    events: list[FeatureValue] = []
    async with AsyncMuninnStreamClient(host=BASE_URL) as client:
        async for event in client.stream(feature="vwap.1m"):
            events.append(event)

    assert [e.feature_name for e in events] == ["vwap.1m", "vwap.1m"]
    assert [float(e.value) for e in events if e.value is not None] == [60000.0, 60005.0]


@respx.mock
async def test_async_stream_maps_http_error() -> None:
    respx.get(STREAM_URL).mock(
        return_value=httpx.Response(404, json={"message": "no such feature"})
    )

    async with AsyncMuninnStreamClient(host=BASE_URL) as client:
        with pytest.raises(MuninnNotFoundError):
            async for _ in client.stream():
                pass

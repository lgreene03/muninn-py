"""Live feature streaming over Server-Sent Events.

Consumes the Muninn server's ``GET /api/v1/features/stream`` (``text/event-stream``)
endpoint, yielding :class:`~muninn.models.FeatureValue` objects as the feature
engine produces them. This is the *push* counterpart to
:meth:`~muninn.client.MuninnClient.get_feature`'s historical *pull*: instead of
polling the warehouse for a bounded range, a researcher attaches to the live tail
and receives each computed value sub-second after its window closes.

The stream is a live tail with no backfill — to get "the last hour, then live",
page the Query API for history and then attach here. See the server-side decision
record at ``muninn/docs/adr/0009-streaming-features-sse.md``.

Two clients mirror the rest of the SDK:

- :class:`MuninnStreamClient` — synchronous; ``stream()`` returns an iterator.
- :class:`AsyncMuninnStreamClient` — asynchronous; ``stream()`` is an async iterator.

Example
-------
.. code-block:: python

    from muninn.streaming import MuninnStreamClient

    with MuninnStreamClient() as stream:
        for event in stream.stream(feature="vwap.1m"):
            print(event.feature_name, event.value, event.window_end)
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator, Mapping

import httpx

from muninn._transport import (
    DEFAULT_HOST,
    DEFAULT_TIMEOUT,
    _build_limits,
    build_base_headers,
    unwrap,
)
from muninn.exceptions import MuninnStreamError, MuninnTimeoutError
from muninn.models import FeatureValue

_STREAM_PATH = "/api/v1/features/stream"
_FEATURE_EVENT = "feature"


def _stream_headers(extra: Mapping[str, str] | None) -> dict[str, str]:
    """Base headers with the SSE ``Accept`` override (callers may still override)."""
    headers: dict[str, str] = {"Accept": "text/event-stream"}
    if extra:
        headers.update(extra)
    return build_base_headers(headers)


def _stream_timeout(timeout: float | httpx.Timeout) -> httpx.Timeout:
    """Bound connect/write/pool but disable the read timeout.

    A live SSE connection is idle between events; with the server's keepalive
    comments it stays warm, but disabling the read timeout means a quiet feature
    (or a paused engine) never aborts the stream. An explicit ``httpx.Timeout`` is
    honored verbatim for callers who want different semantics.
    """
    if isinstance(timeout, httpx.Timeout):
        return timeout
    return httpx.Timeout(connect=timeout, read=None, write=timeout, pool=timeout)


def _parse_feature(data: str) -> FeatureValue:
    try:
        payload = json.loads(data)
    except json.JSONDecodeError as exc:
        raise MuninnStreamError(f"malformed SSE data frame: {exc}") from exc
    return FeatureValue.model_validate(payload)


class _SseDecoder:
    """Incremental Server-Sent Events line decoder.

    Fed one line at a time (no trailing newline, as ``httpx.iter_lines`` yields),
    it returns a ``(event, data)`` tuple when a blank line completes a frame, or
    ``None`` otherwise. Comment lines (starting with ``:``, e.g. keepalives) and
    fields other than ``event``/``data`` are ignored, per the SSE spec.
    """

    def __init__(self) -> None:
        self._event = ""
        self._data: list[str] = []

    def push(self, line: str) -> tuple[str, str] | None:
        if line == "":
            frame: tuple[str, str] | None = None
            if self._data:
                frame = (self._event or "message", "\n".join(self._data))
            self._event = ""
            self._data = []
            return frame
        if line.startswith(":"):
            return None
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "event":
            self._event = value
        elif field == "data":
            self._data.append(value)
        return None


class MuninnStreamClient:
    """Synchronous client for the live feature stream (SSE)."""

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        *,
        timeout: float | httpx.Timeout = DEFAULT_TIMEOUT,
        headers: Mapping[str, str] | None = None,
        max_connections: int | None = None,
        max_keepalive_connections: int | None = None,
        keepalive_expiry: float | None = None,
    ) -> None:
        self._host = host.rstrip("/")
        self._client = httpx.Client(
            base_url=self._host,
            timeout=_stream_timeout(timeout),
            headers=_stream_headers(headers),
            limits=_build_limits(max_connections, max_keepalive_connections, keepalive_expiry),
        )

    def __enter__(self) -> MuninnStreamClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def stream(self, feature: str | None = None) -> Iterator[FeatureValue]:
        """Yield :class:`FeatureValue`s as the engine produces them.

        :param feature: restrict the stream to a single feature name (e.g.
            ``"vwap.1m"``); ``None`` streams every feature.
        :raises MuninnNotFoundError / MuninnValidationError / MuninnAPIError: if the
            server rejects the initial request.
        :raises MuninnTimeoutError: on connection timeout.
        :raises MuninnStreamError: on a malformed event frame.
        """
        params = {"feature": feature} if feature else None
        decoder = _SseDecoder()
        try:
            with self._client.stream("GET", _STREAM_PATH, params=params) as response:
                if response.status_code >= 300:
                    response.read()
                    unwrap(response)  # always raises for a non-2xx status
                for line in response.iter_lines():
                    frame = decoder.push(line)
                    if frame is not None and frame[0] == _FEATURE_EVENT:
                        yield _parse_feature(frame[1])
        except httpx.TimeoutException as exc:
            raise MuninnTimeoutError(str(exc)) from exc


class AsyncMuninnStreamClient:
    """Asynchronous client for the live feature stream (SSE)."""

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        *,
        timeout: float | httpx.Timeout = DEFAULT_TIMEOUT,
        headers: Mapping[str, str] | None = None,
        max_connections: int | None = None,
        max_keepalive_connections: int | None = None,
        keepalive_expiry: float | None = None,
    ) -> None:
        self._host = host.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self._host,
            timeout=_stream_timeout(timeout),
            headers=_stream_headers(headers),
            limits=_build_limits(max_connections, max_keepalive_connections, keepalive_expiry),
        )

    async def __aenter__(self) -> AsyncMuninnStreamClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def stream(self, feature: str | None = None) -> AsyncIterator[FeatureValue]:
        """Async-iterate :class:`FeatureValue`s as the engine produces them.

        See :meth:`MuninnStreamClient.stream` for parameters and raised errors.
        """
        params = {"feature": feature} if feature else None
        decoder = _SseDecoder()
        try:
            async with self._client.stream("GET", _STREAM_PATH, params=params) as response:
                if response.status_code >= 300:
                    await response.aread()
                    unwrap(response)  # always raises for a non-2xx status
                async for line in response.aiter_lines():
                    frame = decoder.push(line)
                    if frame is not None and frame[0] == _FEATURE_EVENT:
                        yield _parse_feature(frame[1])
        except httpx.TimeoutException as exc:
            raise MuninnTimeoutError(str(exc)) from exc

"""Retry policy and helpers shared by both clients.

The SDK retries the requests that are commonly transient on a real
network — connection failures, timeouts, and 5xx responses — and never
retries the responses that won't change on a second attempt — 4xx,
deserialization errors, the typed validation rejections.

Configuration lives in :class:`RetryConfig`. Both ``MuninnClient`` and
``AsyncMuninnClient`` accept either a :class:`RetryConfig` instance or
the equivalent kwargs to their constructor.

Retry attempts use exponential backoff with a small random jitter so
many clients hitting the same flapping endpoint don't all retry on the
same tick.
"""

from __future__ import annotations

import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

import httpx

T = TypeVar("T")


@dataclass(frozen=True)
class RetryConfig:
    """How to retry transient failures.

    Parameters
    ----------
    max_attempts:
        Total attempts including the first try. ``1`` disables retry.
    initial_backoff:
        Seconds before the second attempt.
    max_backoff:
        Cap on backoff between any two attempts.
    backoff_factor:
        Multiplier between successive attempts. Default of 2 gives
        0.1s, 0.2s, 0.4s, 0.8s …
    jitter:
        Uniform random delay added to each backoff in seconds.
    retry_statuses:
        HTTP status codes that should trigger a retry. Default is
        ``(408, 429, 500, 502, 503, 504)`` — the classic transient set.
    """

    max_attempts: int = 3
    initial_backoff: float = 0.1
    max_backoff: float = 10.0
    backoff_factor: float = 2.0
    jitter: float = 0.05
    retry_statuses: tuple[int, ...] = (408, 429, 500, 502, 503, 504)

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.initial_backoff < 0 or self.max_backoff < 0:
            raise ValueError("backoff values must be non-negative")
        if self.backoff_factor <= 0:
            raise ValueError("backoff_factor must be > 0")

    def disabled(self) -> bool:
        return self.max_attempts <= 1

    def delay_for_attempt(self, attempt: int) -> float:
        """Seconds to wait before attempt number ``attempt`` (1-indexed).

        attempt=1 means the first retry (i.e., the second try overall).
        """
        if attempt < 1:
            return 0.0
        base = self.initial_backoff * (self.backoff_factor ** (attempt - 1))
        base = min(base, self.max_backoff)
        return base + random.uniform(0.0, self.jitter)


# Exceptions that mean "the request never reached a useful place;
# retrying is reasonable". The list intentionally excludes
# ``MuninnAPIError`` and its subclasses — those signal a server response
# we already processed (4xx etc.) and shouldn't loop on.
_RETRY_EXC = (
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.RemoteProtocolError,
    httpx.ReadError,
    httpx.WriteError,
    httpx.PoolTimeout,
)


def should_retry_response(response: httpx.Response, config: RetryConfig) -> bool:
    return response.status_code in config.retry_statuses


def should_retry_exception(exc: BaseException) -> bool:
    return isinstance(exc, _RETRY_EXC)


def call_with_retry_sync(
    config: RetryConfig,
    op: Callable[[], httpx.Response],
    *,
    sleeper: Callable[[float], None],
) -> httpx.Response:
    """Run a sync request operation with retry.

    The op should return an ``httpx.Response`` and raise on transport
    failure. Retries the configured exceptions and any response whose
    status code is in ``config.retry_statuses``.
    """
    attempt = 0
    last_exc: BaseException | None = None
    last_response: httpx.Response | None = None

    while attempt < config.max_attempts:
        try:
            response = op()
        except _RETRY_EXC as exc:
            last_exc = exc
            attempt += 1
            if attempt >= config.max_attempts:
                raise
            sleeper(config.delay_for_attempt(attempt))
            continue

        if should_retry_response(response, config):
            last_response = response
            attempt += 1
            if attempt >= config.max_attempts:
                return response
            sleeper(config.delay_for_attempt(attempt))
            continue

        return response

    # Unreachable in normal flow; the loop returns or re-raises before exiting.
    if last_response is not None:
        return last_response
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("retry loop terminated without a result")


async def call_with_retry_async(
    config: RetryConfig,
    op: Callable[[], Awaitable[httpx.Response]],
    *,
    sleeper: Callable[[float], Awaitable[None]],
) -> httpx.Response:
    """Async counterpart of :func:`call_with_retry_sync`."""
    attempt = 0
    last_response: httpx.Response | None = None

    while attempt < config.max_attempts:
        try:
            response = await op()
        except _RETRY_EXC:
            attempt += 1
            if attempt >= config.max_attempts:
                raise
            await sleeper(config.delay_for_attempt(attempt))
            continue

        if should_retry_response(response, config):
            last_response = response
            attempt += 1
            if attempt >= config.max_attempts:
                return response
            await sleeper(config.delay_for_attempt(attempt))
            continue

        return response

    if last_response is not None:
        return last_response
    raise RuntimeError("retry loop terminated without a result")

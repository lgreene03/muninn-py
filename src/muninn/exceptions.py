"""Typed exception hierarchy for the SDK.

Mirrors the Java side's ``MuninnException`` family so that a researcher sees
domain-named errors rather than raw ``httpx`` exceptions. Errors at the
boundary (network, timeout, deserialization) are wrapped; errors from the
server's structured ``{"error": ..., "message": ...}`` response are surfaced
as ``MuninnAPIError`` subclasses.
"""

from __future__ import annotations

from typing import Any


class MuninnError(Exception):
    """Root of all SDK-raised exceptions."""


class MuninnAPIError(MuninnError):
    """The Muninn server returned an unsuccessful HTTP response."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        url: str | None = None,
        body: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.url = url
        self.body = body

    def __str__(self) -> str:
        base = super().__str__()
        return f"[{self.status_code}] {base}" + (f" ({self.url})" if self.url else "")


class MuninnNotFoundError(MuninnAPIError):
    """HTTP 404 — the requested resource (feature, job) does not exist."""


class MuninnValidationError(MuninnAPIError):
    """HTTP 400 — the request was malformed or violated a server-side rule."""


class MuninnTimeoutError(MuninnError):
    """The HTTP call exceeded its configured timeout."""


class MuninnStreamError(MuninnError):
    """The live feature stream ended unexpectedly or delivered a malformed frame."""

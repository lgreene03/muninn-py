"""Shared transport helpers used by both ``MuninnClient`` and ``AsyncMuninnClient``.

Kept private (leading underscore) so the public surface is just the two
client classes. Anything that would be useful to a third-party caller is
re-exported through ``muninn/__init__.py``.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

import httpx
import polars as pl

from muninn._version import __version__
from muninn.exceptions import (
    MuninnAPIError,
    MuninnNotFoundError,
    MuninnValidationError,
)
from muninn.models import FeatureValue

DEFAULT_HOST = "http://localhost:8080"
DEFAULT_TIMEOUT = 30.0
USER_AGENT = f"muninn-py/{__version__}"


def build_base_headers(extra: Mapping[str, str] | None) -> dict[str, str]:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if extra:
        headers.update(extra)
    return headers


def to_iso(value: str | datetime) -> str:
    """Accept either an ISO-8601 string or a ``datetime`` and return a string."""
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def parse_iso(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def extract_rows(payload: Any, *, key: str) -> list[Mapping[str, Any]]:
    """Tolerate either a bare array or a ``{"<key>": [...]}`` envelope."""
    if isinstance(payload, list):
        return list(payload)
    if isinstance(payload, Mapping):
        inner = payload.get(key)
        if isinstance(inner, list):
            return list(inner)
    raise MuninnAPIError(
        f"Expected an array of {key} rows",
        status_code=200,
        body=payload,
    )


def extract_error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text or response.reason_phrase or "Request failed"
    if isinstance(body, Mapping):
        for key in ("message", "error", "detail", "reason"):
            value = body.get(key)
            if isinstance(value, str):
                return value
    return response.reason_phrase or "Request failed"


def unwrap(response: httpx.Response) -> Any:
    """Lift the response into JSON or raise a typed exception.

    Shared between sync and async paths so error mapping is identical.
    """
    if 200 <= response.status_code < 300:
        if not response.content:
            return None
        return response.json()

    message = extract_error_message(response)
    body: Any
    try:
        body = response.json()
    except ValueError:
        body = response.text

    url = str(response.request.url)
    if response.status_code == 404:
        raise MuninnNotFoundError(message, status_code=404, url=url, body=body)
    if response.status_code == 400:
        raise MuninnValidationError(message, status_code=400, url=url, body=body)
    raise MuninnAPIError(message, status_code=response.status_code, url=url, body=body)


def values_to_dataframe(values: list[FeatureValue]) -> pl.DataFrame:
    """Convert ``FeatureValue`` rows into a Polars DataFrame sorted by event time."""
    if not values:
        return pl.DataFrame(
            schema={
                "event_time": pl.Datetime("us", time_zone="UTC"),
                "window_start": pl.Datetime("us", time_zone="UTC"),
                "window_end": pl.Datetime("us", time_zone="UTC"),
                "value": pl.Float64,
                "feature_name": pl.Utf8,
                "feature_version": pl.Utf8,
                "code_version": pl.Utf8,
            }
        )

    rows = [
        {
            "event_time": v.event_time,
            "window_start": v.window_start,
            "window_end": v.window_end,
            "value": float(v.value) if v.value is not None else None,
            "feature_name": v.feature_name,
            "feature_version": v.feature_version,
            "code_version": v.code_version,
        }
        for v in values
    ]
    return pl.DataFrame(rows).sort("event_time")


def feature_value_column(df: pl.DataFrame, *, name: str) -> pl.DataFrame:
    """Project a single-feature frame to the two columns wanted for joining.

    Used by both clients' ``get_features`` to turn each per-feature response
    into ``(event_time, <name>)`` pairs before outer/inner-joining the set.
    """
    return df.select(
        pl.col("event_time"),
        pl.col("value").alias(name),
    )

"""Synchronous HTTP client for the Muninn ``query-api``.

The Muninn server is the Java project at
https://github.com/lgreene03/muninn. This SDK targets the REST endpoints it
exposes under ``/api/v1/``:

- ``GET /api/v1/features/{featureName}`` — feature time-series.
- ``GET /api/v1/features`` — registered feature schemas.
- ``GET /api/v1/replay/jobs`` — list replay jobs.
- ``GET /api/v1/replay/jobs/{jobId}`` — single replay job status.
- ``POST /api/v1/replay/jobs`` — submit a replay job.

The client is designed for notebook ergonomics: zero config to start, return
types are Polars DataFrames (with a one-call escape to Pandas), and errors
are mapped to a typed exception hierarchy.

A separate async client is intentionally deferred. ``httpx`` supports both
sync and async with the same API; promoting this client to async is a small,
mechanical follow-up once a researcher actually needs concurrent fetches.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

import httpx
import polars as pl

from muninn._version import __version__
from muninn.exceptions import (
    MuninnAPIError,
    MuninnNotFoundError,
    MuninnTimeoutError,
    MuninnValidationError,
)
from muninn.models import (
    FeatureDefinition,
    FeatureValue,
    ReplayJob,
    ReplayJobSubmission,
)

_DEFAULT_HOST = "http://localhost:8080"
_DEFAULT_TIMEOUT = 30.0
_USER_AGENT = f"muninn-py/{__version__}"


class MuninnClient:
    """Synchronous Muninn ``query-api`` client.

    Parameters
    ----------
    host:
        Base URL of the Muninn server. Defaults to ``http://localhost:8080``
        — the local-first profile in the main repo.
    timeout:
        Per-request timeout in seconds.
    headers:
        Extra HTTP headers to send on every request (e.g., an auth token if
        an operator has fronted the API with a reverse proxy).

    Example
    -------
    >>> with MuninnClient() as m:
    ...     df = m.get_features(
    ...         instrument="BTC-USDT",
    ...         features=["vwap.1m", "obi"],
    ...         start="2026-05-10T14:00:00Z",
    ...         end="2026-05-10T15:00:00Z",
    ...     )
    """

    def __init__(
        self,
        host: str = _DEFAULT_HOST,
        *,
        timeout: float = _DEFAULT_TIMEOUT,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        base_headers = {"User-Agent": _USER_AGENT, "Accept": "application/json"}
        if headers:
            base_headers.update(headers)
        self._client = httpx.Client(
            base_url=host.rstrip("/"),
            timeout=timeout,
            headers=base_headers,
        )

    # ----- lifecycle -------------------------------------------------------

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> MuninnClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ----- feature discovery -----------------------------------------------

    def list_features(self) -> list[FeatureDefinition]:
        """Return all feature definitions registered on the server.

        Backed by the ``feature_definitions`` table (Flyway migration
        ``V004__feature_definitions.sql`` on the main repo).
        """
        payload = self._get_json("/api/v1/features")
        if not isinstance(payload, list):
            raise MuninnAPIError(
                "Expected an array of feature definitions",
                status_code=200,
                url="/api/v1/features",
                body=payload,
            )
        return [FeatureDefinition.model_validate(row) for row in payload]

    # ----- feature time-series ---------------------------------------------

    def get_feature(
        self,
        feature: str,
        *,
        instrument: str,
        start: str | datetime,
        end: str | datetime,
        limit: int | None = None,
    ) -> pl.DataFrame:
        """Fetch one feature's time-series as a Polars DataFrame.

        Columns: ``event_time``, ``window_start``, ``window_end``, ``value``,
        ``feature_name``, ``feature_version``, ``code_version``.

        Sorted ascending by ``event_time``.
        """
        params: dict[str, Any] = {
            "instrument": instrument,
            "start": _to_iso(start),
            "end": _to_iso(end),
        }
        if limit is not None:
            params["limit"] = limit

        payload = self._get_json(f"/api/v1/features/{feature}", params=params)
        rows = _extract_rows(payload, key="values")
        values = [FeatureValue.model_validate(r) for r in rows]
        return _values_to_dataframe(values)

    def get_features(
        self,
        instrument: str,
        features: Iterable[str],
        start: str | datetime,
        end: str | datetime,
        *,
        limit: int | None = None,
        join: Literal["outer", "inner"] = "outer",
    ) -> pl.DataFrame:
        """Fetch multiple features and join them on ``event_time``.

        Each feature becomes its own column in the returned DataFrame, named
        after the feature (dots in the name preserved). The frame is sorted
        ascending by ``event_time``.

        Parameters
        ----------
        join:
            ``"outer"`` keeps every observed timestamp; missing values become
            ``null``. ``"inner"`` keeps only timestamps present in every
            feature.
        """
        features = list(features)
        if not features:
            raise ValueError("at least one feature name is required")

        frames: list[pl.DataFrame] = []
        for name in features:
            single = self.get_feature(
                name, instrument=instrument, start=start, end=end, limit=limit
            )
            if single.is_empty():
                continue
            # Pivot from long form (one row per emission) to one column named after the feature.
            col = single.select(
                pl.col("event_time"),
                pl.col("value").alias(name),
            )
            frames.append(col)

        if not frames:
            return pl.DataFrame(schema={"event_time": pl.Datetime("us", time_zone="UTC")})

        how: Literal["full", "inner"] = "full" if join == "outer" else "inner"
        merged = frames[0]
        for other in frames[1:]:
            merged = merged.join(other, on="event_time", how=how, coalesce=True)

        return merged.sort("event_time")

    # ----- replay jobs ------------------------------------------------------

    def list_replay_jobs(self) -> list[ReplayJob]:
        """List all replay jobs the server is currently tracking."""
        payload = self._get_json("/api/v1/replay/jobs")
        if not isinstance(payload, list):
            raise MuninnAPIError(
                "Expected an array of replay jobs",
                status_code=200,
                url="/api/v1/replay/jobs",
                body=payload,
            )
        return [ReplayJob.model_validate(row) for row in payload]

    def get_replay_job(self, job_id: str | UUID) -> ReplayJob:
        """Fetch one replay job by id."""
        payload = self._get_json(f"/api/v1/replay/jobs/{job_id}")
        return ReplayJob.model_validate(payload)

    def submit_replay_job(
        self,
        *,
        start: str | datetime,
        end: str | datetime,
        topics: list[str] | None = None,
        feature_version: str | None = None,
    ) -> ReplayJob:
        """Submit a new replay job and return its initial (PENDING) state."""
        submission = ReplayJobSubmission(
            range_from=_parse_iso(start),
            range_to=_parse_iso(end),
            topics=topics,
            feature_version=feature_version,
        )
        response = self._post_json("/api/v1/replay/jobs", json=submission.to_request_body())
        return ReplayJob.model_validate(response)

    # ----- low-level transport ---------------------------------------------

    def _get_json(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        try:
            response = self._client.get(path, params=dict(params or {}))
        except httpx.TimeoutException as exc:
            raise MuninnTimeoutError(f"GET {path} timed out") from exc
        return self._unwrap(response)

    def _post_json(self, path: str, *, json: Any) -> Any:
        try:
            response = self._client.post(path, json=json)
        except httpx.TimeoutException as exc:
            raise MuninnTimeoutError(f"POST {path} timed out") from exc
        return self._unwrap(response)

    @staticmethod
    def _unwrap(response: httpx.Response) -> Any:
        if 200 <= response.status_code < 300:
            if not response.content:
                return None
            return response.json()

        message = _extract_error_message(response)
        body: Any
        try:
            body = response.json()
        except ValueError:
            body = response.text

        if response.status_code == 404:
            raise MuninnNotFoundError(
                message, status_code=404, url=str(response.request.url), body=body
            )
        if response.status_code == 400:
            raise MuninnValidationError(
                message, status_code=400, url=str(response.request.url), body=body
            )
        raise MuninnAPIError(
            message, status_code=response.status_code, url=str(response.request.url), body=body
        )


# ----- helpers --------------------------------------------------------------


def _to_iso(value: str | datetime) -> str:
    """Accept either an ISO-8601 string or a ``datetime`` and return a string."""
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _parse_iso(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _extract_rows(payload: Any, *, key: str) -> list[Mapping[str, Any]]:
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


def _extract_error_message(response: httpx.Response) -> str:
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


def _values_to_dataframe(values: list[FeatureValue]) -> pl.DataFrame:
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

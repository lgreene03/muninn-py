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

For concurrent fetches, use the ``muninn.AsyncMuninnClient`` sibling — same
surface, ``httpx.AsyncClient`` underneath. The sync client also fans
``get_features`` across a thread pool when multiple features are requested.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

import httpx
import polars as pl

from muninn._transport import (
    DEFAULT_HOST,
    DEFAULT_TIMEOUT,
    assemble_panel,
    build_base_headers,
    extract_rows,
    feature_value_column,
    parse_iso,
    to_iso,
    unwrap,
    values_to_dataframe,
)
from muninn.exceptions import MuninnAPIError, MuninnTimeoutError
from muninn.models import (
    FeatureDefinition,
    FeatureValue,
    ReplayJob,
    ReplayJobSubmission,
)


class MuninnClient:
    """Synchronous Muninn ``query-api`` client.

    Parameters
    ----------
    host:
        Base URL of the Muninn server. Defaults to ``http://localhost:8080``.
    timeout:
        Per-request timeout in seconds.
    headers:
        Extra HTTP headers to send on every request (e.g., an auth token if
        an operator has fronted the API with a reverse proxy).
    max_workers:
        Thread-pool size used by :meth:`get_features` when fanning out across
        multiple features. Defaults to ``min(8, len(features))`` per call.

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
        host: str = DEFAULT_HOST,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        headers: Mapping[str, str] | None = None,
        max_workers: int | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=host.rstrip("/"),
            timeout=timeout,
            headers=build_base_headers(headers),
        )
        self._max_workers = max_workers

    # ----- lifecycle -------------------------------------------------------

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> MuninnClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ----- feature discovery -----------------------------------------------

    def list_features(self) -> list[FeatureDefinition]:
        """Return all feature definitions registered on the server."""
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
            "start": to_iso(start),
            "end": to_iso(end),
        }
        if limit is not None:
            params["limit"] = limit

        payload = self._get_json(f"/api/v1/features/{feature}", params=params)
        rows = extract_rows(payload, key="values")
        values = [FeatureValue.model_validate(r) for r in rows]
        return values_to_dataframe(values)

    def get_features(
        self,
        instrument: str,
        features: Iterable[str],
        start: str | datetime,
        end: str | datetime,
        *,
        limit: int | None = None,
        join: Literal["outer", "inner"] = "outer",
        parallel: bool = True,
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
        parallel:
            When ``True`` (default) and more than one feature is requested,
            fans out the GETs across a thread pool. Pass ``False`` to force
            serial fetches — useful when debugging or rate-limiting.
        """
        features = list(features)
        if not features:
            raise ValueError("at least one feature name is required")

        if len(features) == 1 or not parallel:
            raw_results: list[pl.DataFrame | None] = [
                self._fetch_column(
                    name, instrument=instrument, start=start, end=end, limit=limit
                )
                for name in features
            ]
        else:
            workers = self._max_workers or min(8, len(features))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [
                    pool.submit(
                        self._fetch_column,
                        name,
                        instrument=instrument,
                        start=start,
                        end=end,
                        limit=limit,
                    )
                    for name in features
                ]
                raw_results = [f.result() for f in futures]

        frames: list[pl.DataFrame] = [f for f in raw_results if f is not None]
        if not frames:
            return pl.DataFrame(schema={"event_time": pl.Datetime("us", time_zone="UTC")})

        how: Literal["full", "inner"] = "full" if join == "outer" else "inner"
        merged = frames[0]
        for other in frames[1:]:
            merged = merged.join(other, on="event_time", how=how, coalesce=True)
        return merged.sort("event_time")

    def _fetch_column(
        self,
        name: str,
        *,
        instrument: str,
        start: str | datetime,
        end: str | datetime,
        limit: int | None,
    ) -> pl.DataFrame | None:
        single = self.get_feature(
            name, instrument=instrument, start=start, end=end, limit=limit
        )
        if single.is_empty():
            return None
        return feature_value_column(single, name=name)

    def get_panel(
        self,
        instruments: Iterable[str],
        features: Iterable[str],
        start: str | datetime,
        end: str | datetime,
        *,
        limit: int | None = None,
        join: Literal["outer", "inner"] = "outer",
        parallel: bool = True,
    ) -> pl.DataFrame:
        """Fetch a multi-instrument, multi-feature panel as a long-form frame.

        Returns columns ``instrument``, ``event_time``, then one column per
        feature. Rows are sorted by ``(instrument, event_time)``. Each
        instrument's features are joined as in :meth:`get_features`.

        Concurrency
        -----------
        With ``parallel=True`` (default), each instrument's full feature set
        is fetched concurrently with every other instrument via the thread
        pool. Within an instrument, features are also fanned out.
        """
        instruments = list(instruments)
        features = list(features)
        if not instruments:
            raise ValueError("at least one instrument is required")
        if not features:
            raise ValueError("at least one feature name is required")

        def fetch_one(inst: str) -> tuple[str, pl.DataFrame]:
            df = self.get_features(
                instrument=inst,
                features=features,
                start=start,
                end=end,
                limit=limit,
                join=join,
                parallel=parallel,
            )
            return inst, df

        if len(instruments) == 1 or not parallel:
            results = [fetch_one(inst) for inst in instruments]
        else:
            workers = self._max_workers or min(8, len(instruments))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                results = list(pool.map(fetch_one, instruments))

        return assemble_panel(dict(results))

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
            range_from=parse_iso(start),
            range_to=parse_iso(end),
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
        return unwrap(response)

    def _post_json(self, path: str, *, json: Any) -> Any:
        try:
            response = self._client.post(path, json=json)
        except httpx.TimeoutException as exc:
            raise MuninnTimeoutError(f"POST {path} timed out") from exc
        return unwrap(response)

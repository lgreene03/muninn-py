"""Asynchronous counterpart of :class:`MuninnClient`.

Same surface, same exceptions, same return types — ``httpx.AsyncClient``
underneath. The primary win is :meth:`AsyncMuninnClient.get_features`, which
fires every feature GET concurrently via ``asyncio.gather`` instead of the
sync client's thread pool.

For notebook use, prefer the sync client unless you need true cooperative
multitasking (e.g., running inside an existing async framework). The sync
client's thread-pool fan-out already eliminates the serial latency cost for
multi-feature fetches.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
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


class AsyncMuninnClient:
    """Asynchronous Muninn ``query-api`` client.

    Example
    -------
    >>> async with AsyncMuninnClient() as m:
    ...     df = await m.get_features(
    ...         instrument="BTC-USDT",
    ...         features=["vwap.1m", "obi", "vpin"],
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
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=host.rstrip("/"),
            timeout=timeout,
            headers=build_base_headers(headers),
        )
        self._pandas_accessor: Any = None

    # ----- pandas-first surface --------------------------------------------

    @property
    def pandas(self) -> Any:
        """Pandas-flavoured accessor — every coroutine returns ``pandas.DataFrame``.

        See :class:`muninn.pandas_client.AsyncPandasAccessor`.
        """
        if self._pandas_accessor is None:
            from muninn.pandas_client import AsyncPandasAccessor

            self._pandas_accessor = AsyncPandasAccessor(self)
        return self._pandas_accessor

    # ----- lifecycle -------------------------------------------------------

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> AsyncMuninnClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    # ----- feature discovery -----------------------------------------------

    async def list_features(self) -> list[FeatureDefinition]:
        payload = await self._get_json("/api/v1/features")
        if not isinstance(payload, list):
            raise MuninnAPIError(
                "Expected an array of feature definitions",
                status_code=200,
                url="/api/v1/features",
                body=payload,
            )
        return [FeatureDefinition.model_validate(row) for row in payload]

    # ----- feature time-series ---------------------------------------------

    async def get_feature(
        self,
        feature: str,
        *,
        instrument: str,
        start: str | datetime,
        end: str | datetime,
        limit: int | None = None,
    ) -> pl.DataFrame:
        params: dict[str, Any] = {
            "instrument": instrument,
            "start": to_iso(start),
            "end": to_iso(end),
        }
        if limit is not None:
            params["limit"] = limit

        payload = await self._get_json(f"/api/v1/features/{feature}", params=params)
        rows = extract_rows(payload, key="values")
        values = [FeatureValue.model_validate(r) for r in rows]
        return values_to_dataframe(values)

    async def get_features(
        self,
        instrument: str,
        features: Iterable[str],
        start: str | datetime,
        end: str | datetime,
        *,
        limit: int | None = None,
        join: Literal["outer", "inner"] = "outer",
    ) -> pl.DataFrame:
        """Fetch multiple features concurrently and join them on ``event_time``."""
        features = list(features)
        if not features:
            raise ValueError("at least one feature name is required")

        tasks = [
            self._fetch_column(name, instrument=instrument, start=start, end=end, limit=limit)
            for name in features
        ]
        results = await asyncio.gather(*tasks)
        frames = [df for df in results if df is not None]

        if not frames:
            return pl.DataFrame(schema={"event_time": pl.Datetime("us", time_zone="UTC")})

        how: Literal["full", "inner"] = "full" if join == "outer" else "inner"
        merged = frames[0]
        for other in frames[1:]:
            merged = merged.join(other, on="event_time", how=how, coalesce=True)
        return merged.sort("event_time")

    async def _fetch_column(
        self,
        name: str,
        *,
        instrument: str,
        start: str | datetime,
        end: str | datetime,
        limit: int | None,
    ) -> pl.DataFrame | None:
        single = await self.get_feature(
            name, instrument=instrument, start=start, end=end, limit=limit
        )
        if single.is_empty():
            return None
        return feature_value_column(single, name=name)

    async def get_panel(
        self,
        instruments: Iterable[str],
        features: Iterable[str],
        start: str | datetime,
        end: str | datetime,
        *,
        limit: int | None = None,
        join: Literal["outer", "inner"] = "outer",
    ) -> pl.DataFrame:
        """Fetch a multi-instrument, multi-feature panel via ``asyncio.gather``.

        Returns columns ``instrument``, ``event_time``, then one column per
        feature. Rows are sorted by ``(instrument, event_time)``.
        """
        instruments = list(instruments)
        features = list(features)
        if not instruments:
            raise ValueError("at least one instrument is required")
        if not features:
            raise ValueError("at least one feature name is required")

        async def fetch_one(inst: str) -> tuple[str, pl.DataFrame]:
            df = await self.get_features(
                instrument=inst,
                features=features,
                start=start,
                end=end,
                limit=limit,
                join=join,
            )
            return inst, df

        results = await asyncio.gather(*(fetch_one(i) for i in instruments))
        return assemble_panel(dict(results))

    # ----- replay jobs ------------------------------------------------------

    async def list_replay_jobs(self) -> list[ReplayJob]:
        payload = await self._get_json("/api/v1/replay/jobs")
        if not isinstance(payload, list):
            raise MuninnAPIError(
                "Expected an array of replay jobs",
                status_code=200,
                url="/api/v1/replay/jobs",
                body=payload,
            )
        return [ReplayJob.model_validate(row) for row in payload]

    async def get_replay_job(self, job_id: str | UUID) -> ReplayJob:
        payload = await self._get_json(f"/api/v1/replay/jobs/{job_id}")
        return ReplayJob.model_validate(payload)

    async def submit_replay_job(
        self,
        *,
        start: str | datetime,
        end: str | datetime,
        topics: list[str] | None = None,
        feature_version: str | None = None,
    ) -> ReplayJob:
        submission = ReplayJobSubmission(
            range_from=parse_iso(start),
            range_to=parse_iso(end),
            topics=topics,
            feature_version=feature_version,
        )
        response = await self._post_json(
            "/api/v1/replay/jobs", json=submission.to_request_body()
        )
        return ReplayJob.model_validate(response)

    # ----- low-level transport ---------------------------------------------

    async def _get_json(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        try:
            response = await self._client.get(path, params=dict(params or {}))
        except httpx.TimeoutException as exc:
            raise MuninnTimeoutError(f"GET {path} timed out") from exc
        return unwrap(response)

    async def _post_json(self, path: str, *, json: Any) -> Any:
        try:
            response = await self._client.post(path, json=json)
        except httpx.TimeoutException as exc:
            raise MuninnTimeoutError(f"POST {path} timed out") from exc
        return unwrap(response)

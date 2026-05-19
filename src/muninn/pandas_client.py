"""Pandas-first accessors for both clients.

Polars is the SDK's default DataFrame; ``muninn.MuninnClient.pandas`` (and
its async sibling) provides the same surface but returns ``pandas.DataFrame``
on every call. The mapping is mechanical — call the underlying Polars
method and ``.to_pandas()`` the result. The accessor exists primarily so
research notebooks already wedded to pandas don't have to scatter
``.to_pandas()`` at every call site.

The accessor is read-only — there's no construction path. Reach it from
an existing client:

.. code-block:: python

    with MuninnClient() as m:
        df = m.pandas.get_features(
            instrument="BTC-USDT",
            features=["vwap.1m", "obi"],
            start="2026-05-10T14:00:00Z",
            end="2026-05-10T15:00:00Z",
        )
        df.head()  # pandas.DataFrame
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import TYPE_CHECKING, Literal

import pandas as pd
import polars as pl

if TYPE_CHECKING:
    from muninn.async_client import AsyncMuninnClient
    from muninn.client import MuninnClient


def _to_pandas(df: pl.DataFrame) -> pd.DataFrame:
    """Convert a Polars frame to pandas without requiring pyarrow.

    ``polars.DataFrame.to_pandas()`` defaults to a pyarrow-backed path
    that fails when the optional ``pyarrow`` dependency isn't installed.
    Going through ``to_dicts()`` adds a hop but avoids the hard
    dependency, and the frames the SDK handles are bounded by the
    server's pagination so the overhead is negligible.
    """
    if df.is_empty():
        return pd.DataFrame(columns=df.columns)
    return pd.DataFrame(df.to_dicts())


class PandasAccessor:
    """Pandas-flavoured surface for :class:`muninn.MuninnClient`.

    Wraps the parent client; every method delegates to the parent and
    converts the returned Polars frame to pandas before returning.
    """

    __slots__ = ("_client",)

    def __init__(self, client: MuninnClient) -> None:
        self._client = client

    def get_feature(
        self,
        feature: str,
        *,
        instrument: str,
        start: str | datetime,
        end: str | datetime,
        limit: int | None = None,
    ) -> pd.DataFrame:
        df = self._client.get_feature(
            feature, instrument=instrument, start=start, end=end, limit=limit
        )
        return _to_pandas(df)

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
    ) -> pd.DataFrame:
        df = self._client.get_features(
            instrument=instrument,
            features=features,
            start=start,
            end=end,
            limit=limit,
            join=join,
            parallel=parallel,
        )
        return _to_pandas(df)

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
    ) -> pd.DataFrame:
        df = self._client.get_panel(
            instruments=instruments,
            features=features,
            start=start,
            end=end,
            limit=limit,
            join=join,
            parallel=parallel,
        )
        return _to_pandas(df)


class AsyncPandasAccessor:
    """Pandas-flavoured surface for :class:`muninn.AsyncMuninnClient`."""

    __slots__ = ("_client",)

    def __init__(self, client: AsyncMuninnClient) -> None:
        self._client = client

    async def get_feature(
        self,
        feature: str,
        *,
        instrument: str,
        start: str | datetime,
        end: str | datetime,
        limit: int | None = None,
    ) -> pd.DataFrame:
        df = await self._client.get_feature(
            feature, instrument=instrument, start=start, end=end, limit=limit
        )
        return _to_pandas(df)

    async def get_features(
        self,
        instrument: str,
        features: Iterable[str],
        start: str | datetime,
        end: str | datetime,
        *,
        limit: int | None = None,
        join: Literal["outer", "inner"] = "outer",
    ) -> pd.DataFrame:
        df = await self._client.get_features(
            instrument=instrument,
            features=features,
            start=start,
            end=end,
            limit=limit,
            join=join,
        )
        return _to_pandas(df)

    async def get_panel(
        self,
        instruments: Iterable[str],
        features: Iterable[str],
        start: str | datetime,
        end: str | datetime,
        *,
        limit: int | None = None,
        join: Literal["outer", "inner"] = "outer",
    ) -> pd.DataFrame:
        df = await self._client.get_panel(
            instruments=instruments,
            features=features,
            start=start,
            end=end,
            limit=limit,
            join=join,
        )
        return _to_pandas(df)

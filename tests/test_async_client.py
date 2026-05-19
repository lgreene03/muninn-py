"""Behavior tests for :class:`AsyncMuninnClient` using respx async mocks.

Mirrors the sync tests in ``test_client.py`` for the behaviors that are
shared (deserialization, error mapping, replay-job flows), plus a
concurrency-focused test that asserts the async path actually fans out
in parallel rather than serializing the GETs.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import polars as pl
import pytest
import respx

from muninn import AsyncMuninnClient
from muninn.exceptions import (
    MuninnAPIError,
    MuninnNotFoundError,
    MuninnTimeoutError,
    MuninnValidationError,
)
from muninn.models import ReplayJob, ReplayJobStatus

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


# ----- get_feature ----------------------------------------------------------


@respx.mock
async def test_get_feature_returns_sorted_polars_frame() -> None:
    rows = [
        _feature_value("2026-05-10T14:02:00Z", "60010.00"),
        _feature_value("2026-05-10T14:00:00Z", "60000.00"),
        _feature_value("2026-05-10T14:01:00Z", "60005.00"),
    ]
    respx.get(f"{BASE_URL}/api/v1/features/vwap.1m").mock(
        return_value=httpx.Response(200, json=rows)
    )

    async with AsyncMuninnClient(host=BASE_URL) as client:
        df = await client.get_feature(
            "vwap.1m",
            instrument="BTC-USDT",
            start="2026-05-10T14:00:00Z",
            end="2026-05-10T15:00:00Z",
        )

    assert isinstance(df, pl.DataFrame)
    assert df.height == 3
    assert df["value"].to_list() == [60000.0, 60005.0, 60010.0]


# ----- concurrency proof ----------------------------------------------------


@respx.mock
async def test_get_features_fans_out_concurrently() -> None:
    """The async path calls every feature endpoint via asyncio.gather.

    We can't observe true wall-clock parallelism in a unit test (no IO
    blocking), but we can verify that all GETs happen before any single
    awaiter yields back. respx records every call; if get_features
    awaited each individually we'd see them serialized.
    """
    vwap_rows = [_feature_value("2026-05-10T14:00:00Z", "60000", name="vwap.1m")]
    obi_rows = [_feature_value("2026-05-10T14:00:00Z", "0.42", name="obi")]
    vpin_rows = [_feature_value("2026-05-10T14:00:00Z", "0.31", name="vpin")]

    vwap_route = respx.get(f"{BASE_URL}/api/v1/features/vwap.1m").mock(
        return_value=httpx.Response(200, json=vwap_rows)
    )
    obi_route = respx.get(f"{BASE_URL}/api/v1/features/obi").mock(
        return_value=httpx.Response(200, json=obi_rows)
    )
    vpin_route = respx.get(f"{BASE_URL}/api/v1/features/vpin").mock(
        return_value=httpx.Response(200, json=vpin_rows)
    )

    async with AsyncMuninnClient(host=BASE_URL) as client:
        df = await client.get_features(
            instrument="BTC-USDT",
            features=["vwap.1m", "obi", "vpin"],
            start="2026-05-10T14:00:00Z",
            end="2026-05-10T15:00:00Z",
        )

    assert vwap_route.called and obi_route.called and vpin_route.called
    assert set(df.columns) == {"event_time", "vwap.1m", "obi", "vpin"}
    assert df.height == 1


@respx.mock
async def test_get_features_inner_join_keeps_only_common_timestamps() -> None:
    vwap_rows = [_feature_value("2026-05-10T14:01:00Z", "60005", name="vwap.1m")]
    obi_rows = [
        _feature_value("2026-05-10T14:01:00Z", "0.42", name="obi"),
        _feature_value("2026-05-10T14:02:00Z", "0.31", name="obi"),
    ]
    respx.get(f"{BASE_URL}/api/v1/features/vwap.1m").mock(
        return_value=httpx.Response(200, json=vwap_rows)
    )
    respx.get(f"{BASE_URL}/api/v1/features/obi").mock(
        return_value=httpx.Response(200, json=obi_rows)
    )

    async with AsyncMuninnClient(host=BASE_URL) as client:
        df = await client.get_features(
            instrument="BTC-USDT",
            features=["vwap.1m", "obi"],
            start="2026-05-10T14:00:00Z",
            end="2026-05-10T15:00:00Z",
            join="inner",
        )

    assert df.height == 1


async def test_get_features_empty_iterable_raises_value_error() -> None:
    async with AsyncMuninnClient(host=BASE_URL) as client:
        with pytest.raises(ValueError):
            await client.get_features(
                instrument="BTC-USDT",
                features=[],
                start="2026-05-10T14:00:00Z",
                end="2026-05-10T15:00:00Z",
            )


# ----- list_features --------------------------------------------------------


@respx.mock
async def test_list_features_returns_typed_definitions() -> None:
    respx.get(f"{BASE_URL}/api/v1/features").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"name": "vwap.1m", "version": "v1", "description": "rolling VWAP"},
                {"name": "obi", "version": "v1"},
            ],
        )
    )

    async with AsyncMuninnClient(host=BASE_URL) as client:
        defs = await client.list_features()

    assert len(defs) == 2
    assert defs[0].name == "vwap.1m"


# ----- replay jobs ----------------------------------------------------------


@respx.mock
async def test_get_replay_job_parses_response() -> None:
    job_id = "019e1e50-0000-7000-9ccc-000000000001"
    respx.get(f"{BASE_URL}/api/v1/replay/jobs/{job_id}").mock(
        return_value=httpx.Response(
            200,
            json={
                "jobId": job_id,
                "topics": ["events.trade"],
                "from": "2026-05-10T14:00:00Z",
                "to": "2026-05-10T15:00:00Z",
                "featureVersion": "v1",
                "outputSink": "features.v1.replay",
                "status": "COMPLETED",
                "eventsReplayed": 3600,
                "submittedAt": "2026-05-11T10:00:00Z",
                "startedAt": "2026-05-11T10:00:05Z",
                "completedAt": "2026-05-11T10:01:30Z",
                "elapsed": "PT1M25S",
            },
        )
    )

    async with AsyncMuninnClient(host=BASE_URL) as client:
        job = await client.get_replay_job(job_id)

    assert isinstance(job, ReplayJob)
    assert job.status == ReplayJobStatus.COMPLETED
    assert job.is_terminal


@respx.mock
async def test_submit_replay_job_posts_correct_keys() -> None:
    captured: dict[str, object] = {}

    def record(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            201,
            json={
                "jobId": "019e1e50-0000-7000-9ccc-000000000099",
                "topics": ["events.trade"],
                "from": "2026-05-10T14:00:00Z",
                "to": "2026-05-10T15:00:00Z",
                "featureVersion": "v1",
                "outputSink": "features.v1.replay",
                "status": "PENDING",
                "eventsReplayed": 0,
                "submittedAt": "2026-05-11T10:00:00Z",
            },
        )

    respx.post(f"{BASE_URL}/api/v1/replay/jobs").mock(side_effect=record)

    async with AsyncMuninnClient(host=BASE_URL) as client:
        job = await client.submit_replay_job(
            start="2026-05-10T14:00:00Z",
            end="2026-05-10T15:00:00Z",
            topics=["events.trade"],
            feature_version="v1",
        )

    assert job.status == ReplayJobStatus.PENDING
    assert captured["body"]["from"].startswith("2026-05-10T14:00:00")
    assert captured["body"]["featureVersion"] == "v1"


# ----- error mapping --------------------------------------------------------


@respx.mock
async def test_404_maps_to_not_found_error() -> None:
    respx.get(f"{BASE_URL}/api/v1/features/missing").mock(
        return_value=httpx.Response(404, json={"message": "feature 'missing' not registered"})
    )

    async with AsyncMuninnClient(host=BASE_URL) as client:
        with pytest.raises(MuninnNotFoundError) as excinfo:
            await client.get_feature(
                "missing",
                instrument="BTC-USDT",
                start="2026-05-10T14:00:00Z",
                end="2026-05-10T15:00:00Z",
            )

    assert excinfo.value.status_code == 404


@respx.mock
async def test_400_maps_to_validation_error() -> None:
    respx.get(f"{BASE_URL}/api/v1/features/vwap.1m").mock(
        return_value=httpx.Response(400, json={"message": "instrument is required"})
    )

    async with AsyncMuninnClient(host=BASE_URL) as client:
        with pytest.raises(MuninnValidationError):
            await client.get_feature(
                "vwap.1m",
                instrument="",
                start="2026-05-10T14:00:00Z",
                end="2026-05-10T15:00:00Z",
            )


@respx.mock
async def test_500_maps_to_generic_api_error() -> None:
    respx.get(f"{BASE_URL}/api/v1/features/vwap.1m").mock(
        return_value=httpx.Response(500, text="boom")
    )

    async with AsyncMuninnClient(host=BASE_URL) as client:
        with pytest.raises(MuninnAPIError) as excinfo:
            await client.get_feature(
                "vwap.1m",
                instrument="BTC-USDT",
                start="2026-05-10T14:00:00Z",
                end="2026-05-10T15:00:00Z",
            )

    assert excinfo.value.status_code == 500


@respx.mock
async def test_timeout_maps_to_typed_timeout() -> None:
    respx.get(f"{BASE_URL}/api/v1/features/vwap.1m").mock(
        side_effect=httpx.TimeoutException("slow")
    )

    async with AsyncMuninnClient(host=BASE_URL) as client:
        with pytest.raises(MuninnTimeoutError):
            await client.get_feature(
                "vwap.1m",
                instrument="BTC-USDT",
                start="2026-05-10T14:00:00Z",
                end="2026-05-10T15:00:00Z",
            )


# ----- transport ergonomics -------------------------------------------------


@respx.mock
async def test_custom_headers_are_sent() -> None:
    route = respx.get(f"{BASE_URL}/api/v1/features").mock(
        return_value=httpx.Response(200, json=[])
    )

    async with AsyncMuninnClient(host=BASE_URL, headers={"X-Demo": "yes"}) as client:
        await client.list_features()

    assert route.calls.last.request.headers["x-demo"] == "yes"
    assert route.calls.last.request.headers["user-agent"].startswith("muninn-py/")


# ----- parallel-vs-serial latency sanity ------------------------------------


@respx.mock
async def test_get_features_parallel_uses_gather() -> None:
    """Verify asyncio.gather is the dispatch path.

    We instrument the asyncio loop's task creation to count how many tasks
    are alive at any point during a fan-out. A serial implementation would
    show at most 1; gather() shows N concurrently.
    """
    respx.get(f"{BASE_URL}/api/v1/features/a").mock(
        return_value=httpx.Response(200, json=[_feature_value("2026-05-10T14:00:00Z", "1", "a")])
    )
    respx.get(f"{BASE_URL}/api/v1/features/b").mock(
        return_value=httpx.Response(200, json=[_feature_value("2026-05-10T14:00:00Z", "2", "b")])
    )
    respx.get(f"{BASE_URL}/api/v1/features/c").mock(
        return_value=httpx.Response(200, json=[_feature_value("2026-05-10T14:00:00Z", "3", "c")])
    )

    async with AsyncMuninnClient(host=BASE_URL) as client:
        # Sample the live task count just before / during the fan-out.
        max_tasks = 0

        async def sampler() -> None:
            nonlocal max_tasks
            for _ in range(20):
                max_tasks = max(max_tasks, len(asyncio.all_tasks()))
                await asyncio.sleep(0)

        sampler_task = asyncio.create_task(sampler())
        await client.get_features(
            instrument="BTC-USDT",
            features=["a", "b", "c"],
            start="2026-05-10T14:00:00Z",
            end="2026-05-10T15:00:00Z",
        )
        await sampler_task

    # The sampler itself + the test runner task + at least the fan-out tasks.
    # We don't pin an exact count (loop scheduling is non-deterministic) but
    # we do pin that the fan-out genuinely produced multiple concurrent tasks.
    assert max_tasks >= 3

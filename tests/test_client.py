"""Behavior tests for ``MuninnClient`` using respx to mock the HTTP layer."""

from __future__ import annotations

import json

import httpx
import polars as pl
import pytest
import respx

from muninn import MuninnClient
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
def test_get_feature_returns_sorted_polars_frame() -> None:
    rows = [
        _feature_value("2026-05-10T14:02:00Z", "60010.00"),
        _feature_value("2026-05-10T14:00:00Z", "60000.00"),
        _feature_value("2026-05-10T14:01:00Z", "60005.00"),
    ]
    respx.get(f"{BASE_URL}/api/v1/features/vwap.1m").mock(
        return_value=httpx.Response(200, json=rows)
    )

    with MuninnClient(host=BASE_URL) as client:
        df = client.get_feature(
            "vwap.1m",
            instrument="BTC-USDT",
            start="2026-05-10T14:00:00Z",
            end="2026-05-10T15:00:00Z",
        )

    assert isinstance(df, pl.DataFrame)
    assert df.height == 3
    assert df["value"].to_list() == [60000.0, 60005.0, 60010.0]
    assert df.columns[:2] == ["event_time", "window_start"]


@respx.mock
def test_get_feature_accepts_envelope_with_values_key() -> None:
    """Server might return ``{"values": [...]}``; tolerate it."""
    rows = [_feature_value("2026-05-10T14:00:00Z", "1")]
    respx.get(f"{BASE_URL}/api/v1/features/vwap.1m").mock(
        return_value=httpx.Response(200, json={"values": rows, "meta": {"count": 1}})
    )

    with MuninnClient(host=BASE_URL) as client:
        df = client.get_feature(
            "vwap.1m",
            instrument="BTC-USDT",
            start="2026-05-10T14:00:00Z",
            end="2026-05-10T15:00:00Z",
        )

    assert df.height == 1


@respx.mock
def test_get_feature_empty_response_returns_typed_empty_frame() -> None:
    respx.get(f"{BASE_URL}/api/v1/features/vwap.1m").mock(
        return_value=httpx.Response(200, json=[])
    )

    with MuninnClient(host=BASE_URL) as client:
        df = client.get_feature(
            "vwap.1m",
            instrument="BTC-USDT",
            start="2026-05-10T14:00:00Z",
            end="2026-05-10T15:00:00Z",
        )

    assert df.height == 0
    assert "event_time" in df.columns


# ----- get_features (multi, joined) -----------------------------------------


@respx.mock
def test_get_features_outer_joins_on_event_time() -> None:
    vwap_rows = [
        _feature_value("2026-05-10T14:00:00Z", "60000", name="vwap.1m"),
        _feature_value("2026-05-10T14:01:00Z", "60005", name="vwap.1m"),
    ]
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

    with MuninnClient(host=BASE_URL) as client:
        df = client.get_features(
            instrument="BTC-USDT",
            features=["vwap.1m", "obi"],
            start="2026-05-10T14:00:00Z",
            end="2026-05-10T15:00:00Z",
        )

    assert set(df.columns) == {"event_time", "vwap.1m", "obi"}
    assert df.height == 3  # 14:00, 14:01, 14:02 outer-joined


@respx.mock
def test_get_features_inner_join_keeps_only_common_timestamps() -> None:
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

    with MuninnClient(host=BASE_URL) as client:
        df = client.get_features(
            instrument="BTC-USDT",
            features=["vwap.1m", "obi"],
            start="2026-05-10T14:00:00Z",
            end="2026-05-10T15:00:00Z",
            join="inner",
        )

    assert df.height == 1


def test_get_features_empty_iterable_raises_value_error() -> None:
    with MuninnClient(host=BASE_URL) as client, pytest.raises(ValueError):
        client.get_features(
            instrument="BTC-USDT",
            features=[],
            start="2026-05-10T14:00:00Z",
            end="2026-05-10T15:00:00Z",
        )


# ----- list_features --------------------------------------------------------


@respx.mock
def test_list_features_returns_typed_definitions() -> None:
    respx.get(f"{BASE_URL}/api/v1/features").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"name": "vwap.1m", "version": "v1", "description": "rolling VWAP"},
                {"name": "obi", "version": "v1"},
            ],
        )
    )

    with MuninnClient(host=BASE_URL) as client:
        defs = client.list_features()

    assert len(defs) == 2
    assert defs[0].name == "vwap.1m"
    assert defs[0].description == "rolling VWAP"


# ----- replay jobs ----------------------------------------------------------


@respx.mock
def test_get_replay_job_parses_response() -> None:
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

    with MuninnClient(host=BASE_URL) as client:
        job = client.get_replay_job(job_id)

    assert isinstance(job, ReplayJob)
    assert job.status == ReplayJobStatus.COMPLETED
    assert job.is_terminal


@respx.mock
def test_submit_replay_job_posts_correct_keys() -> None:
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

    with MuninnClient(host=BASE_URL) as client:
        job = client.submit_replay_job(
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
def test_404_maps_to_not_found_error() -> None:
    respx.get(f"{BASE_URL}/api/v1/features/missing").mock(
        return_value=httpx.Response(404, json={"message": "feature 'missing' not registered"})
    )

    with MuninnClient(host=BASE_URL) as client, pytest.raises(MuninnNotFoundError) as excinfo:
        client.get_feature(
            "missing",
            instrument="BTC-USDT",
            start="2026-05-10T14:00:00Z",
            end="2026-05-10T15:00:00Z",
        )

    assert "not registered" in str(excinfo.value)
    assert excinfo.value.status_code == 404


@respx.mock
def test_400_maps_to_validation_error() -> None:
    respx.get(f"{BASE_URL}/api/v1/features/vwap.1m").mock(
        return_value=httpx.Response(400, json={"message": "instrument is required"})
    )

    with MuninnClient(host=BASE_URL) as client, pytest.raises(MuninnValidationError):
        client.get_feature(
            "vwap.1m",
            instrument="",
            start="2026-05-10T14:00:00Z",
            end="2026-05-10T15:00:00Z",
        )


@respx.mock
def test_500_maps_to_generic_api_error() -> None:
    respx.get(f"{BASE_URL}/api/v1/features/vwap.1m").mock(
        return_value=httpx.Response(500, text="boom")
    )

    with MuninnClient(host=BASE_URL) as client, pytest.raises(MuninnAPIError) as excinfo:
        client.get_feature(
            "vwap.1m",
            instrument="BTC-USDT",
            start="2026-05-10T14:00:00Z",
            end="2026-05-10T15:00:00Z",
        )

    assert excinfo.value.status_code == 500


@respx.mock
def test_timeout_maps_to_typed_timeout() -> None:
    respx.get(f"{BASE_URL}/api/v1/features/vwap.1m").mock(
        side_effect=httpx.TimeoutException("slow")
    )

    with MuninnClient(host=BASE_URL) as client, pytest.raises(MuninnTimeoutError):
        client.get_feature(
            "vwap.1m",
            instrument="BTC-USDT",
            start="2026-05-10T14:00:00Z",
            end="2026-05-10T15:00:00Z",
        )


# ----- transport ergonomics -------------------------------------------------


@respx.mock
def test_custom_headers_are_sent() -> None:
    route = respx.get(f"{BASE_URL}/api/v1/features").mock(
        return_value=httpx.Response(200, json=[])
    )

    with MuninnClient(host=BASE_URL, headers={"X-Demo": "yes"}) as client:
        client.list_features()

    assert route.calls.last.request.headers["x-demo"] == "yes"
    ua = route.calls.last.request.headers["user-agent"]
    assert ua.startswith("muninn-py/")

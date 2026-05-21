"""Performance benchmarks for MuninnClient critical paths.

These benchmarks guard against regressions in the response-parsing and
DataFrame-construction code, which is the hot path for every SDK call.

Usage
-----
Run benchmarks locally:
    pytest tests/bench_client.py --benchmark-only -v

Save a baseline (run once after landing a clean state):
    pytest tests/bench_client.py --benchmark-save=baseline --benchmark-only

Compare against that baseline (CI regression gate):
    pytest tests/bench_client.py --benchmark-compare=baseline --benchmark-fail-max-delta-mean=0.25

The 0.25 threshold means CI fails if mean latency regresses more than 25 %.
"""

from __future__ import annotations

import json
import re

import httpx
import pytest
import respx

from muninn import MuninnClient

BASE_URL = "http://muninn.bench"
_N_ROWS = 10_000


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _make_rows(n: int) -> list[dict]:
    rows = []
    for i in range(n):
        # Spread across ~7 days of minutes so sort order is non-trivial.
        day = (i // 1440) % 7 + 1
        hour = (i // 60) % 24
        minute = i % 60
        ts = f"2026-01-{day:02d}T{hour:02d}:{minute:02d}:00Z"
        rows.append(
            {
                "eventId": f"019e1e50-7979-7000-9ccc-{i:012x}",
                "eventTime": ts,
                "featureName": "vwap.1m",
                "featureVersion": "v1",
                "value": str(60_000.0 + i * 0.1),
                "windowStart": ts,
                "windowEnd": ts,
                "inputEventIds": [],
                "codeVersion": "dev",
            }
        )
    return rows


@pytest.fixture(scope="session")
def _rows_10k_json() -> bytes:
    """Pre-serialised 10K-row response — built once per session."""
    return json.dumps(_make_rows(_N_ROWS)).encode()


@pytest.fixture()
def _mock_feature(_rows_10k_json: bytes):
    """respx router that serves the pre-built 10K payload for any feature URL."""
    with respx.mock(assert_all_called=False) as router:
        router.get(re.compile(r".*/api/v1/features/.*")).mock(
            return_value=httpx.Response(
                200,
                content=_rows_10k_json,
                headers={"Content-Type": "application/json"},
            )
        )
        yield router


# ---------------------------------------------------------------------------
# Benchmarks
# ---------------------------------------------------------------------------


def test_bench_get_feature_10k_rows(benchmark, _mock_feature) -> None:
    """get_feature: parse and sort a 10K-row response into a Polars DataFrame.

    This is the primary regression guard. A >25% increase in mean latency on
    this path indicates a regression in JSON deserialisation, schema validation,
    or Polars construction.
    """
    with MuninnClient(host=BASE_URL) as client:
        result = benchmark(
            client.get_feature,
            "vwap.1m",
            instrument="BTC-USDT",
            start="2026-01-01T00:00:00Z",
            end="2026-01-08T00:00:00Z",
        )

    assert result.height == _N_ROWS


def test_bench_get_features_serial_3x(benchmark, _rows_10k_json) -> None:
    """get_features serial: three features fetched sequentially (parallel=False).

    Measures the join and DataFrame-construction overhead for multi-feature
    calls. Benchmarked in serial mode to keep respx mocking thread-safe.
    """
    with respx.mock(assert_all_called=False) as router:
        # Use exact URL pattern (no anchored regex) so query-string params
        # don't break the route match.
        for name in ("vwap.1m", "obi", "vpin"):
            router.get(re.compile(rf".*/api/v1/features/{re.escape(name)}")).mock(
                return_value=httpx.Response(
                    200,
                    content=_rows_10k_json,
                    headers={"Content-Type": "application/json"},
                )
            )

        with MuninnClient(host=BASE_URL) as client:
            result = benchmark(
                client.get_features,
                instrument="BTC-USDT",
                features=["vwap.1m", "obi", "vpin"],
                start="2026-01-01T00:00:00Z",
                end="2026-01-08T00:00:00Z",
                parallel=False,
            )

    # get_features outer-joins on event_time; all three mocked responses carry
    # identical timestamps, so the join yields exactly _N_ROWS.
    assert result.height == _N_ROWS

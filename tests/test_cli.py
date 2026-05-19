"""Tests for the ``muninn`` CLI.

Uses Click's :class:`CliRunner` to drive the command tree and respx to
mock HTTP. The CLI surfaces the same client; these tests confirm that
flags, environment variables, and output formatting work end-to-end.
"""

from __future__ import annotations

import json

import httpx
import respx
from click.testing import CliRunner

from muninn.cli import cli

BASE_URL = "http://muninn.test"


def _runner_env() -> dict[str, str]:
    return {"MUNINN_HOST": BASE_URL}


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


# ----- features list --------------------------------------------------------


@respx.mock
def test_features_list_json_default() -> None:
    respx.get(f"{BASE_URL}/api/v1/features").mock(
        return_value=httpx.Response(
            200,
            json=[{"name": "vwap.1m", "version": "v1"}, {"name": "obi", "version": "v1"}],
        )
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["features", "list"], env=_runner_env())

    assert result.exit_code == 0
    body = json.loads(result.output)
    assert len(body) == 2
    assert body[0]["name"] == "obi" or body[0]["name"] == "vwap.1m"


@respx.mock
def test_features_list_table_format() -> None:
    respx.get(f"{BASE_URL}/api/v1/features").mock(
        return_value=httpx.Response(200, json=[{"name": "vwap.1m", "version": "v1"}])
    )

    runner = CliRunner()
    result = runner.invoke(
        cli, ["--format", "table", "features", "list"], env=_runner_env()
    )

    assert result.exit_code == 0
    assert "vwap.1m" in result.output


# ----- features get ---------------------------------------------------------


@respx.mock
def test_features_get_outputs_rows() -> None:
    respx.get(f"{BASE_URL}/api/v1/features/vwap.1m").mock(
        return_value=httpx.Response(
            200,
            json=[
                _feature_value("2026-05-10T14:00:00Z", "60000"),
                _feature_value("2026-05-10T14:01:00Z", "60010"),
            ],
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "features",
            "get",
            "vwap.1m",
            "--instrument",
            "BTC-USDT",
            "--start",
            "2026-05-10T14:00:00Z",
            "--end",
            "2026-05-10T15:00:00Z",
        ],
        env=_runner_env(),
    )

    assert result.exit_code == 0
    body = json.loads(result.output)
    assert len(body) == 2


# ----- replay submit / status / list ----------------------------------------


@respx.mock
def test_replay_submit_returns_pending_job() -> None:
    respx.post(f"{BASE_URL}/api/v1/replay/jobs").mock(
        return_value=httpx.Response(
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
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "replay",
            "submit",
            "--start",
            "2026-05-10T14:00:00Z",
            "--end",
            "2026-05-10T15:00:00Z",
            "--feature-version",
            "v1",
        ],
        env=_runner_env(),
    )

    assert result.exit_code == 0
    body = json.loads(result.output)
    assert body["status"] == "PENDING"


@respx.mock
def test_replay_status_prints_job() -> None:
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
                "eventsReplayed": 100,
                "submittedAt": "2026-05-11T10:00:00Z",
            },
        )
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["replay", "status", job_id], env=_runner_env())

    assert result.exit_code == 0
    body = json.loads(result.output)
    assert body["status"] == "COMPLETED"


@respx.mock
def test_replay_list_returns_array() -> None:
    respx.get(f"{BASE_URL}/api/v1/replay/jobs").mock(
        return_value=httpx.Response(200, json=[])
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["replay", "list"], env=_runner_env())

    assert result.exit_code == 0
    assert json.loads(result.output) == []


# ----- top-level options ----------------------------------------------------


def test_version_flag_prints_version() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "muninn" in result.output.lower()


@respx.mock
def test_host_override_via_flag() -> None:
    other = "http://other.muninn.test"
    respx.get(f"{other}/api/v1/features").mock(
        return_value=httpx.Response(200, json=[])
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["--host", other, "features", "list"])

    assert result.exit_code == 0
    assert json.loads(result.output) == []

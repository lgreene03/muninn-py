"""Contract tests for the pydantic models — round-trip against canonical JSON."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from muninn.models import (
    FeatureDefinition,
    FeatureValue,
    ReplayJob,
    ReplayJobStatus,
    ReplayJobSubmission,
)


def test_feature_value_parses_canonical_shape() -> None:
    payload = {
        "eventId": "019e1e50-7979-7000-9ccc-e4e309080a2c",
        "eventTime": "2026-05-10T14:01:00Z",
        "featureName": "vwap.1m",
        "featureVersion": "v1",
        "value": "60007.78",
        "windowStart": "2026-05-10T14:00:00Z",
        "windowEnd": "2026-05-10T14:01:00Z",
        "inputEventIds": [
            "019e1e50-0001-7000-9ccc-000000000001",
            "019e1e50-0002-7000-9ccc-000000000002",
        ],
        "codeVersion": "dev",
    }

    fv = FeatureValue.model_validate(payload)

    assert fv.event_id == UUID("019e1e50-7979-7000-9ccc-e4e309080a2c")
    assert fv.feature_name == "vwap.1m"
    assert fv.value == Decimal("60007.78")
    assert fv.window_start == datetime(2026, 5, 10, 14, 0, tzinfo=timezone.utc)
    assert len(fv.input_event_ids) == 2
    assert fv.code_version == "dev"


def test_feature_value_supports_map_outputs() -> None:
    payload = {
        "eventId": "019e1e50-7979-7000-9ccc-e4e309080a2c",
        "eventTime": "2026-05-10T14:01:00Z",
        "featureName": "obi",
        "featureVersion": "v1",
        "values": {"depth_1": "0.42", "depth_5": "0.18"},
        "windowStart": "2026-05-10T14:00:00Z",
        "windowEnd": "2026-05-10T14:01:00Z",
        "inputEventIds": [],
        "codeVersion": "dev",
    }

    fv = FeatureValue.model_validate(payload)
    assert fv.value is None
    assert fv.values is not None
    assert fv.values["depth_1"] == Decimal("0.42")


def test_feature_value_ignores_unknown_fields() -> None:
    """Schema-evolution rule: server can add nullable fields; SDK keeps working."""
    payload = {
        "eventId": "019e1e50-7979-7000-9ccc-e4e309080a2c",
        "eventTime": "2026-05-10T14:01:00Z",
        "featureName": "vwap.1m",
        "featureVersion": "v1",
        "value": "1",
        "windowStart": "2026-05-10T14:00:00Z",
        "windowEnd": "2026-05-10T14:01:00Z",
        "inputEventIds": [],
        "codeVersion": "dev",
        "newlyAddedNullableField": "ignored",
    }

    fv = FeatureValue.model_validate(payload)
    assert fv.value == Decimal("1")


def test_replay_job_parses_with_from_alias() -> None:
    payload = {
        "jobId": "019e1e50-0000-7000-9ccc-000000000001",
        "topics": ["events.trade"],
        "from": "2026-05-10T14:00:00Z",
        "to": "2026-05-10T15:00:00Z",
        "featureVersion": "v1",
        "outputSink": "features.v1.replay",
        "status": "RUNNING",
        "eventsReplayed": 1234,
        "submittedAt": "2026-05-11T10:00:00Z",
        "startedAt": "2026-05-11T10:00:05Z",
        "completedAt": None,
        "elapsed": None,
        "error": None,
    }

    job = ReplayJob.model_validate(payload)
    assert job.range_from == datetime(2026, 5, 10, 14, 0, tzinfo=timezone.utc)
    assert job.status == ReplayJobStatus.RUNNING
    assert job.is_terminal is False


@pytest.mark.parametrize(
    "status,terminal",
    [
        (ReplayJobStatus.PENDING, False),
        (ReplayJobStatus.RUNNING, False),
        (ReplayJobStatus.COMPLETED, True),
        (ReplayJobStatus.FAILED, True),
    ],
)
def test_replay_job_terminal_flag(status: ReplayJobStatus, terminal: bool) -> None:
    job = ReplayJob.model_validate(
        {
            "jobId": "019e1e50-0000-7000-9ccc-000000000001",
            "topics": ["events.trade"],
            "from": "2026-05-10T14:00:00Z",
            "to": "2026-05-10T15:00:00Z",
            "featureVersion": "v1",
            "outputSink": "features.v1.replay",
            "status": status.value,
            "eventsReplayed": 0,
            "submittedAt": "2026-05-11T10:00:00Z",
        }
    )
    assert job.is_terminal is terminal


def test_replay_job_submission_renders_server_keys() -> None:
    sub = ReplayJobSubmission(
        range_from=datetime(2026, 5, 10, 14, 0, tzinfo=timezone.utc),
        range_to=datetime(2026, 5, 10, 15, 0, tzinfo=timezone.utc),
        topics=["events.trade"],
        feature_version="v1",
    )

    body = sub.to_request_body()

    assert body["from"].startswith("2026-05-10T14:00:00")
    assert body["to"].startswith("2026-05-10T15:00:00")
    assert body["topics"] == ["events.trade"]
    assert body["featureVersion"] == "v1"


def test_feature_definition_minimal_payload() -> None:
    fd = FeatureDefinition.model_validate({"name": "vwap.1m", "version": "v1"})
    assert fd.name == "vwap.1m"
    assert fd.version == "v1"
    assert fd.description is None

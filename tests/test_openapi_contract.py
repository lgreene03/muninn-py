"""OpenAPI contract test.

Loads the recorded snapshot at tests/testdata/muninn_api_docs_v1.json and
asserts that every endpoint the SDK calls exists with the parameters the SDK
sends.

This is a static offline test — no server needed. Its job is to catch the
class of bug where the server renames a field and the SDK silently reads a
zero or None: e.g. the server renames ``eventTime`` → ``event_time``, or
moves ``from`` → ``rangeFrom`` in the replay request body. JSON's
``extra="ignore"`` semantics (Pydantic's default for these models) would let
such a rename through without raising an exception; this test catches it at PR
time instead.

To re-record the spec from a running Muninn server:
    curl -s http://localhost:8080/api-docs | python -m json.tool \
        > tests/testdata/muninn_api_docs_v1.json
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

SPEC_PATH = Path(__file__).parent / "testdata" / "muninn_api_docs_v1.json"


@pytest.fixture(scope="module")
def spec() -> dict[str, Any]:
    return json.loads(SPEC_PATH.read_text())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _path_params(path_item: dict[str, Any], method: str) -> dict[str, Any]:
    """Return {param_name: param_obj} for an operation's query parameters."""
    operation: dict[str, Any] = path_item.get(method, {})
    return {
        p["name"]: p
        for p in operation.get("parameters", [])
        if p.get("in") == "query"
    }


def _schema_props(spec: dict[str, Any], schema_ref: str) -> set[str]:
    """Resolve a $ref like '#/components/schemas/Foo' and return its property names."""
    name = schema_ref.split("/")[-1]
    schema = spec["components"]["schemas"][name]
    return set(schema.get("properties", {}).keys())


# ---------------------------------------------------------------------------
# Endpoint existence
# ---------------------------------------------------------------------------


class TestEndpointExistence:
    """Every path the SDK calls must exist in the spec."""

    def test_list_features_path_exists(self, spec: dict[str, Any]) -> None:
        assert "/api/v1/features" in spec["paths"], (
            "GET /api/v1/features missing from spec — MuninnClient.list_features() will break"
        )

    def test_get_feature_path_exists(self, spec: dict[str, Any]) -> None:
        assert "/api/v1/features/{featureName}" in spec["paths"], (
            "GET /api/v1/features/{featureName} missing — MuninnClient.get_feature() will break"
        )

    def test_list_replay_jobs_path_exists(self, spec: dict[str, Any]) -> None:
        assert "/api/v1/replay/jobs" in spec["paths"], (
            "GET /api/v1/replay/jobs missing — MuninnClient.list_replay_jobs() will break"
        )

    def test_get_replay_job_path_exists(self, spec: dict[str, Any]) -> None:
        assert "/api/v1/replay/jobs/{jobId}" in spec["paths"], (
            "GET /api/v1/replay/jobs/{jobId} missing — MuninnClient.get_replay_job() will break"
        )

    def test_submit_replay_job_path_exists(self, spec: dict[str, Any]) -> None:
        assert "post" in spec["paths"]["/api/v1/replay/jobs"], (
            "POST /api/v1/replay/jobs missing — MuninnClient.submit_replay_job() will break"
        )


# ---------------------------------------------------------------------------
# Query parameter contract for GET /api/v1/features/{featureName}
# ---------------------------------------------------------------------------


class TestGetFeatureParameters:
    """The SDK sends instrument, start, end, and optionally limit.
    If the server renames any of these the request will silently use the wrong param.
    """

    @pytest.fixture(autouse=True)
    def _params(self, spec: dict[str, Any]) -> None:
        self.params = _path_params(
            spec["paths"]["/api/v1/features/{featureName}"], "get"
        )

    def test_instrument_param_declared(self) -> None:
        assert "instrument" in self.params, (
            "Query param 'instrument' missing from GET /api/v1/features/{featureName}"
        )

    def test_start_param_declared(self) -> None:
        assert "start" in self.params, (
            "Query param 'start' missing from GET /api/v1/features/{featureName}"
        )

    def test_end_param_declared(self) -> None:
        assert "end" in self.params, (
            "Query param 'end' missing from GET /api/v1/features/{featureName}"
        )

    def test_instrument_is_required(self) -> None:
        assert self.params["instrument"].get("required") is True, (
            "Query param 'instrument' should be required"
        )

    def test_start_is_required(self) -> None:
        assert self.params["start"].get("required") is True

    def test_end_is_required(self) -> None:
        assert self.params["end"].get("required") is True

    def test_limit_param_declared(self) -> None:
        assert "limit" in self.params, (
            "Query param 'limit' missing — optional but SDK sends it when set"
        )


# ---------------------------------------------------------------------------
# FeatureValue response schema — camelCase field names
# ---------------------------------------------------------------------------


class TestFeatureValueSchema:
    """The SDK's FeatureValue model uses Field(alias=...) to map camelCase
    server fields to snake_case Python attrs. If a field is renamed server-side
    the alias silently returns None; these tests catch that.
    """

    SDK_REQUIRED_FIELDS = {
        "eventId",
        "eventTime",
        "featureName",
        "featureVersion",
        "windowStart",
        "windowEnd",
        "codeVersion",
    }

    def test_all_required_fields_in_schema(self, spec: dict[str, Any]) -> None:
        props = _schema_props(spec, "#/components/schemas/FeatureValue")
        missing = self.SDK_REQUIRED_FIELDS - props
        assert not missing, (
            f"FeatureValue schema is missing fields the SDK reads: {missing!r}. "
            "If the server renamed one of these, FeatureValue.model_validate() "
            "will silently produce None for the affected column."
        )


# ---------------------------------------------------------------------------
# ReplayJob response schema
# ---------------------------------------------------------------------------


class TestReplayJobSchema:
    """SDK ReplayJob uses aliases: from→range_from, to→range_to, etc."""

    SDK_REQUIRED_FIELDS = {
        "jobId",
        "topics",
        "status",
        "from",
        "to",
        "featureVersion",
        "submittedAt",
    }

    def test_all_required_fields_in_schema(self, spec: dict[str, Any]) -> None:
        props = _schema_props(spec, "#/components/schemas/ReplayJob")
        missing = self.SDK_REQUIRED_FIELDS - props
        assert not missing, (
            f"ReplayJob schema is missing fields the SDK reads: {missing!r}"
        )


# ---------------------------------------------------------------------------
# ReplayJobSubmission request body
# ---------------------------------------------------------------------------


class TestReplayJobSubmissionSchema:
    """submit_replay_job() sends {'from': ..., 'to': ..., ...}.
    If the server renames 'from'/'to' the job will be created with null range.
    """

    SDK_SENT_FIELDS = {"from", "to"}

    def test_request_body_fields_in_schema(self, spec: dict[str, Any]) -> None:
        props = _schema_props(spec, "#/components/schemas/ReplayJobSubmission")
        missing = self.SDK_SENT_FIELDS - props
        assert not missing, (
            f"ReplayJobSubmission schema missing fields SDK sends: {missing!r}"
        )

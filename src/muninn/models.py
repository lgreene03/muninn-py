"""Pydantic models mirroring Muninn's server-side JSON contracts.

Models are intentionally lenient (``extra="ignore"``) so an older SDK keeps
working when the server adds a nullable field — the schema-evolution rule from
the Muninn project's ``EVENT_SCHEMA_STRATEGY.md``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class _MuninnModel(BaseModel):
    """Shared config: ignore unknown fields; allow population by alias or name."""

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
        str_strip_whitespace=True,
    )


class ReplayJobStatus(str, Enum):
    """Lifecycle states of a replay job (matches Java ``ReplayJobStatus``)."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class FeatureValue(_MuninnModel):
    """One emitted ``FeatureComputedEvent`` row.

    The server's canonical schema is documented in
    ``docs/steering/DOMAIN_MODEL.md §FeatureComputedEvent`` on the main repo.
    Per ADR-0002 there, ``eventId`` is provenance metadata and not part of the
    determinism claim; the SDK exposes it but consumers should not rely on
    cross-run equality of that field alone.
    """

    event_id: UUID = Field(alias="eventId")
    event_time: datetime = Field(alias="eventTime")
    feature_name: str = Field(alias="featureName")
    feature_version: str = Field(alias="featureVersion")
    value: Decimal | None = None
    values: dict[str, Decimal] | None = None
    window_start: datetime = Field(alias="windowStart")
    window_end: datetime = Field(alias="windowEnd")
    input_event_ids: list[UUID] = Field(default_factory=list, alias="inputEventIds")
    code_version: str = Field(alias="codeVersion")


class FeatureDefinition(_MuninnModel):
    """Schema descriptor returned by the discovery endpoint.

    The Muninn server registers feature definitions in PostgreSQL (Flyway
    migration ``V004__feature_definitions.sql``). This model is the SDK's
    view of that row, narrowed to what a researcher needs.
    """

    name: str
    version: str
    description: str | None = None
    output_kind: str | None = Field(default=None, alias="outputKind")
    window_duration: str | None = Field(default=None, alias="windowDuration")
    code_version: str | None = Field(default=None, alias="codeVersion")


class ReplayJob(_MuninnModel):
    """A submitted or completed replay job (matches Java ``ReplayJob``)."""

    job_id: UUID = Field(alias="jobId")
    topics: list[str]
    range_from: datetime = Field(alias="from")
    range_to: datetime = Field(alias="to")
    feature_version: str = Field(alias="featureVersion")
    output_sink: str = Field(alias="outputSink")
    status: ReplayJobStatus
    events_replayed: int = Field(default=0, alias="eventsReplayed")
    submitted_at: datetime = Field(alias="submittedAt")
    started_at: datetime | None = Field(default=None, alias="startedAt")
    completed_at: datetime | None = Field(default=None, alias="completedAt")
    elapsed: timedelta | None = None
    error: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in (ReplayJobStatus.COMPLETED, ReplayJobStatus.FAILED)


class ReplayJobSubmission(_MuninnModel):
    """Request body for ``POST /api/v1/replay/jobs``."""

    topics: list[str] | None = None
    range_from: datetime = Field(alias="from")
    range_to: datetime = Field(alias="to")
    feature_version: str | None = Field(default=None, alias="featureVersion")

    def to_request_body(self) -> dict[str, Any]:
        """Render as the server-expected JSON keys."""
        body: dict[str, Any] = {
            "from": self.range_from.isoformat(),
            "to": self.range_to.isoformat(),
        }
        if self.topics is not None:
            body["topics"] = self.topics
        if self.feature_version is not None:
            body["featureVersion"] = self.feature_version
        return body

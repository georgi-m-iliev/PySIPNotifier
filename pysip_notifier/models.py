from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class JobStatus(StrEnum):
    QUEUED = "queued"
    SYNTHESIZING = "synthesizing"
    CALLING = "calling"
    COMPLETED = "completed"
    FAILED = "failed"


class NotifyRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    message: str = Field(min_length=1, max_length=4000)
    destination: str | None = Field(default=None, alias="to", max_length=128)
    voice: str | None = Field(default=None, max_length=128)
    repetitions: int | None = Field(default=None, ge=1, le=5)


class Job(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    status: JobStatus = JobStatus.QUEUED
    message: str
    destination: str
    voice: str
    repetitions: int
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None


class JobAccepted(BaseModel):
    id: UUID
    status: JobStatus


class HealthResponse(BaseModel):
    status: str
    sip_configured: bool
    sip_ready: bool
    queued_calls: int

"""Validated wire contracts for trusted Runtime token accounting."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RuntimeTokenUsageEvent(BaseModel):
    """One provider call or the terminal marker for an admitted Runtime run."""

    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    kind: Literal["usage", "finalize"]
    occurred_at: datetime
    model_name: str | None = Field(default=None, max_length=255)
    request_type: str | None = Field(default=None, max_length=64)
    input_tokens: int | None = Field(default=None, ge=0, le=2_000_000_000)
    output_tokens: int | None = Field(default=None, ge=0, le=2_000_000_000)
    total_tokens: int | None = Field(default=None, ge=0, le=2_000_000_000)
    usage_source: Literal["usage_metadata", "response_metadata", "estimated"] | None = None
    usage_event_ids: list[UUID] = Field(default_factory=list, max_length=2_000)

    @model_validator(mode="after")
    def validate_kind_fields(self) -> "RuntimeTokenUsageEvent":
        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must include a timezone")

        if self.kind == "usage":
            required = {
                "model_name": self.model_name,
                "request_type": self.request_type,
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "total_tokens": self.total_tokens,
                "usage_source": self.usage_source,
            }
            missing = sorted(key for key, value in required.items() if value is None)
            if missing:
                raise ValueError(f"usage events require: {', '.join(missing)}")
            assert self.input_tokens is not None
            assert self.output_tokens is not None
            assert self.total_tokens is not None
            if self.total_tokens < self.input_tokens + self.output_tokens:
                raise ValueError("total_tokens must cover input_tokens + output_tokens")
            if self.usage_event_ids:
                raise ValueError("usage events cannot contain usage_event_ids")
        else:
            usage_only_values = (
                self.model_name,
                self.request_type,
                self.input_tokens,
                self.output_tokens,
                self.total_tokens,
                self.usage_source,
            )
            if any(value is not None for value in usage_only_values):
                raise ValueError("finalize events cannot contain token usage fields")
            if len(set(self.usage_event_ids)) != len(self.usage_event_ids):
                raise ValueError("usage_event_ids must be unique")
        return self


class RuntimeTokenUsageEnvelope(BaseModel):
    """Runtime report authenticated by a backend-issued, run-scoped context."""

    model_config = ConfigDict(extra="forbid")

    usage_context: str = Field(..., min_length=32, max_length=4096)
    event: RuntimeTokenUsageEvent

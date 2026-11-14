"""Validated AI investigation requests and responses."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class InvestigationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    question: str = Field(
        default="Summarize the incident evidence and remaining uncertainty.", max_length=2000
    )


class EvidenceClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    statement: str = Field(min_length=1, max_length=2000)
    evidence_ids: list[UUID] = Field(min_length=1, max_length=20)


class AIInvestigationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    executive_summary: str = Field(min_length=1, max_length=4000)
    observations: list[EvidenceClaim] = Field(max_length=30)
    hypotheses: list[EvidenceClaim] = Field(max_length=30)
    uncertainty: list[str] = Field(max_length=30)
    next_steps: list[str] = Field(max_length=20)

    @model_validator(mode="after")
    def nonempty_observations(self) -> AIInvestigationResponse:
        if not self.observations and not self.uncertainty:
            raise ValueError("response must state observations or uncertainty")
        return self


class AIInvestigationView(BaseModel):
    id: UUID
    incident_id: UUID
    job_id: UUID
    status: str
    response: AIInvestigationResponse | None
    provider: str | None
    model: str | None
    failure_class: str | None
    failure_message: str | None
    created_at: datetime
    completed_at: datetime | None


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: str
    messages: list[ChatMessage]
    temperature: float = 0
    response_format: dict[str, str] = {"type": "json_object"}


class ChatChoice(BaseModel):
    message: ChatMessage


class ChatCompletion(BaseModel):
    choices: list[ChatChoice] = Field(min_length=1)

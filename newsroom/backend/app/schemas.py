from typing import Literal

from pydantic import BaseModel, Field


class Fact(BaseModel):
    id: str
    content: str
    juiciness: int = Field(ge=1, le=5)
    guard: int = Field(ge=0, le=5)
    unlock_hint: str
    tell: str
    partial: str | None


class Persona(BaseModel):
    id: str
    name: str
    verbosity: int = Field(ge=1, le=5)
    evasiveness: int = Field(ge=1, le=5)
    hostility: int = Field(ge=1, le=5)
    pressure_response: Literal["standard", "inverse"]
    speech_style: str
    deflections: list[str]


class Dossier(BaseModel):
    scenario_id: str
    topic: str
    surface_bio: str
    persona: Persona
    facts: list[Fact]
    red_lines: list[str]


class FactState(BaseModel):
    fact_id: str
    guard_current: int = Field(ge=0, le=5)
    consecutive_probes: int = Field(ge=0)
    revealed: Literal["hidden", "partial", "full"]


class GuestOutput(BaseModel):
    pressure: int = Field(ge=0, le=5)
    targeted_fact: str | None
    action: Literal["reveal", "partial", "tell", "deflect"]
    speech: str
    stage_direction: str


class DirectorHint(BaseModel):
    should_speak: bool
    urgency: int = Field(ge=1, le=3)
    type: Literal["追问", "换角度", "打断他", "让他说完", "收尾", "别问了"]
    hint: str = Field(max_length=15)


class WriterCritique(BaseModel):
    approved: bool
    guard_gradient_ok: bool
    unlock_hints_actionable: bool
    surface_bio_consistent: bool
    issues: list[str]

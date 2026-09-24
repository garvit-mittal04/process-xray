"""Shared data models used by every agent in the pipeline."""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class Message(BaseModel):
    """One message from a chat export, after redaction."""
    timestamp: datetime
    sender: str                      # pseudonym, e.g. "Person 1"
    text: str
    has_attachment: bool = False


class ResponseStat(BaseModel):
    responder: str
    replies: int
    median_minutes: float
    slowest_minutes: float


class ChatDigest(BaseModel):
    """What the parser hands to the Mapper agent."""
    source_name: str
    date_range: tuple[datetime, datetime]
    total_messages: int
    participants: dict[str, int]                 # pseudonym -> message count
    response_stats: list[ResponseStat]
    follow_up_count: int                         # "any update?" style chasers
    follow_up_examples: list[str]
    attachment_count: int
    busiest_hours: list[int]                     # top 3 hours of day
    messages: list[Message]


class ProcessStep(BaseModel):
    """One step in the reconstructed process (filled by later agents)."""
    id: str
    name: str
    actor: str
    system: str
    input: str
    output: str
    frequency_per_week: Optional[float] = None
    minutes_per_run: Optional[float] = None
    error_rate: Optional[float] = None
    wait_before_hours: Optional[float] = None
    assumed_fields: list[str] = Field(default_factory=list)
    scores: dict[str, int] = Field(default_factory=dict)
    recommendation: Optional[str] = None


class ProcessState(BaseModel):
    """The single object every agent reads and writes."""
    process_name: str
    steps: list[ProcessStep] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

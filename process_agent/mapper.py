"""Mapper agent: turns a ChatDigest into a structured process map.

The model cites message numbers as evidence for every step. Frequency and
wait times are then measured in code from real timestamps (see measure.py),
so the only guessed number is hands-on minutes per step."""
from typing import Literal

from pydantic import BaseModel, Field

from .llm import generate_json
from .schema import ChatDigest


class MappedStep(BaseModel):
    id: str                                   # "S1", "S2", ...
    name: str
    actor: str                                # e.g. "Person 2 (office staff)"
    system: str                               # e.g. "WhatsApp", "Tally", "Phone"
    input: str
    output: str
    minutes_per_run: float                    # hands-on effort (estimated)
    starts_on_external_event: bool = False    # e.g. a customer order arrives
    timing_depends_on_outsiders: bool = False # customers, transporters, banks, physical work
    message_ids: list[int] = Field(default_factory=list)
    # Filled in by measure.py, not by the model
    frequency_per_week: float = 0.0
    wait_before_hours: float = 0.0            # working hours waited before this step
    assumed_fields: list[str] = Field(default_factory=list)


class PainPoint(BaseModel):
    kind: Literal[
        "waiting", "rework", "duplicate_entry", "manual_chasing", "manual_lookup", "other"
    ]
    description: str
    step_ids: list[str]
    message_ids: list[int] = Field(default_factory=list)


class ProcessMap(BaseModel):
    process_name: str
    summary: str
    roles: dict[str, str]                     # "Person 1" -> "Owner"
    steps: list[MappedStep]
    pain_points: list[PainPoint]
    mermaid: str


SYSTEM = """You are an expert operations analyst for small Indian businesses.
You read redacted WhatsApp group chats (often in Hinglish) and reconstruct the
real business process people follow. Be precise and grounded: every step and
pain point must be supported by numbered messages in the chat. Refer to
customers generically ("Customer A", "Customer B"), never by name.
Reply with JSON only."""

INSTRUCTIONS = """Reconstruct the process shown in this chat. Messages are numbered #0, #1, ...

Return a JSON object with exactly these keys:
- "process_name": short name, e.g. "Order to payment"
- "summary": 2-3 sentences describing how the process runs today
- "roles": map each participant ("Person 1") to their likely role
- "steps": ordered list; each step has
    "id" ("S1", "S2"...), "name", "actor" (e.g. "Person 2 (office staff)"),
    "system" (tool used: WhatsApp, Tally, phone, email, godown visit...),
    "input", "output",
    "minutes_per_run" (your estimate of hands-on effort per occurrence),
    "starts_on_external_event" (true only if the step is triggered by something
        outside the team, such as a new customer order),
    "timing_depends_on_outsiders" (true if the wait before this step mostly depends
        on customers, transporters, suppliers, banks or physical work, e.g. a
        payment arriving or a truck collecting goods),
    "message_ids" (the message numbers where this step happens, ONE message
        per occurrence, e.g. [7, 25] if it happened twice)
- "pain_points": list; each has "kind" (one of waiting, rework, duplicate_entry,
    manual_chasing, manual_lookup, other), "description", "step_ids",
    "message_ids" (supporting message numbers)
- "mermaid": a valid Mermaid flowchart ("flowchart TD") of the steps using ids
    S1, S2... as node ids, with labels in quotes. Include decision points
    such as partial stock. No styling.

Aim for 8-12 steps. Merge tiny steps done by the same person in the same tool,
but keep "payment received" separate from "chasing payment", and keep internal
dispatch instructions separate from the physical dispatch.
Do not invent steps that the chat does not show."""


MAPPER_SAMPLE = 250   # messages; enough to learn the process structure


def format_transcript(d: ChatDigest, limit: int | None = None) -> str:
    start, end = d.date_range
    days = max((end - start).days, 1)
    lines = [
        f"Chat: {d.source_name}",
        f"Period: {start:%d %b %Y} to {end:%d %b %Y} ({days} days)",
        "Participants: " + ", ".join(f"{p} ({n} msgs)" for p, n in d.participants.items()),
        "",
        "Transcript:",
    ]
    msgs = d.messages if limit is None else d.messages[:limit]
    if limit is not None and len(d.messages) > limit:
        lines.insert(-1, f"(Showing the first {limit} of {len(d.messages)} messages; the rest follow the same process.)")
    for i, m in enumerate(msgs):
        text = m.text.replace("\n", " / ")
        lines.append(f"#{i} [{m.timestamp:%a %d %b %H:%M}] {m.sender}: {text}")
    return "\n".join(lines)


def map_process(digest: ChatDigest) -> ProcessMap:
    prompt = INSTRUCTIONS + "\n\n" + format_transcript(digest, limit=MAPPER_SAMPLE)
    return generate_json(SYSTEM, prompt, ProcessMap)

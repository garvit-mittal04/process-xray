"""Mapper agent: turns a ChatDigest into a structured process map."""
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
    frequency_per_week: float
    minutes_per_run: float
    wait_before_hours: float                  # typical wait before this step starts
    assumed_fields: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)   # short message references


class PainPoint(BaseModel):
    kind: Literal[
        "waiting", "rework", "duplicate_entry", "manual_chasing", "manual_lookup", "other"
    ]
    description: str
    step_ids: list[str]
    evidence: list[str]


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
pain point must be supported by messages in the chat. Refer to customers
generically ("Customer A", "Customer B"), never by name. Reply with JSON only."""

INSTRUCTIONS = """Reconstruct the process shown in this chat.

Return a JSON object with exactly these keys:
- "process_name": short name, e.g. "Order to payment"
- "summary": 2-3 sentences describing how the process runs today
- "roles": map each participant ("Person 1") to their likely role
- "steps": ordered list; each step has
    "id" ("S1", "S2"...), "name", "actor" (e.g. "Person 2 (office staff)"),
    "system" (tool used: WhatsApp, Tally, phone, email, godown visit...),
    "input", "output",
    "frequency_per_week" (estimate from how often it happens in the chat period),
    "minutes_per_run" (hands-on effort estimate),
    "wait_before_hours" (typical delay before this step starts, from timestamps),
    "assumed_fields" (list every numeric field that is an estimate rather than
      directly measured from timestamps; minutes_per_run is almost always assumed),
    "evidence" (1-3 short references like "07 Mar 13:30 Person 2: reminder for approval")
- "pain_points": list; each has "kind" (one of waiting, rework, duplicate_entry,
    manual_chasing, manual_lookup, other), "description", "step_ids", "evidence"
- "mermaid": a valid Mermaid flowchart ("flowchart TD") of the steps using ids
    S1, S2... as node ids, with labels in quotes. Include decision points
    such as partial stock. No styling.

Split steps so each has one actor and one system. Do not invent steps that the
chat does not show."""


def _format_digest(d: ChatDigest) -> str:
    start, end = d.date_range
    days = max((end - start).days, 1)
    lines = [
        f"Chat: {d.source_name}",
        f"Period: {start:%d %b %Y} to {end:%d %b %Y} ({days} days)",
        f"Messages: {d.total_messages}",
        "Participants: " + ", ".join(f"{p} ({n} msgs)" for p, n in d.participants.items()),
        "Median reply times: " + ", ".join(
            f"{s.responder} {s.median_minutes / 60:.1f}h" for s in d.response_stats
        ),
        f"Follow-up/chasing messages: {d.follow_up_count}",
        f"Files shared: {d.attachment_count}",
        "",
        "Transcript:",
    ]
    for m in d.messages:
        text = m.text.replace("\n", " / ")
        lines.append(f"[{m.timestamp:%d %b %H:%M}] {m.sender}: {text}")
    return "\n".join(lines)


def map_process(digest: ChatDigest) -> ProcessMap:
    prompt = INSTRUCTIONS + "\n\n" + _format_digest(digest)
    return generate_json(SYSTEM, prompt, ProcessMap)

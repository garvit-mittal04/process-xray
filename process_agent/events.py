"""Event-log agent: groups the chat into cases (one per order or request) and
tags which message marks each step. This is the chat equivalent of the event
log that enterprise process-mining tools extract from ERP systems.

Long chats are read in chunks. Between chunks the agent is reminded of the
open cases, so an order that starts in one chunk and is paid weeks later in
another stays one case. Code does the stitching and validation."""
from pydantic import BaseModel, Field

from .llm import generate_json
from .mapper import ProcessMap
from .schema import ChatDigest

CHUNK_SIZE = 120            # messages per call
MAX_REPEAT_DAYS = 3         # a step may repeat within a case only within this many days


class CaseEvent(BaseModel):
    step_id: str
    message_id: int


class Case(BaseModel):
    case_id: str                                  # "C1", "C2", ...
    label: str                                    # e.g. "Customer A - 40 cartons atta"
    events: list[CaseEvent] = Field(default_factory=list)
    chaser_message_ids: list[int] = Field(default_factory=list)   # reminders about this case


class ChunkCase(BaseModel):
    case_id: str                                  # existing id, or "NEW" for a new case
    label: str = ""
    hint: str = ""                                # private matching details, never shown
    events: list[CaseEvent] = Field(default_factory=list)
    chaser_message_ids: list[int] = Field(default_factory=list)


class ChunkResult(BaseModel):
    cases: list[ChunkCase]


class EventLog(BaseModel):
    cases: list[Case]


SYSTEM = """You are a process-mining specialist. You turn chat conversations
into clean event logs: every business case (an order, request or job) and the
exact messages where each process step happened for that case. Messages about
different cases are interleaved, so track each case by customer, invoice
number, product and quantity. Reply with JSON only."""

INSTRUCTIONS = """You are reading one part of a longer chat. Using the process
steps and the list of cases already open from earlier parts, record what
happens in THIS part only.

For every case with activity in this part, return:
- "case_id": the id of an open case if this activity belongs to it, else "NEW"
- "label": for NEW cases only, a short description without names
- "hint": for NEW cases only, private matching details (customer, product,
   quantity, invoice number) so later parts can recognise the case
- "events": [{"step_id", "message_id"}] - the ONE message where each step is
   actually done for that case (not a reminder asking for it)
- "chaser_message_ids": messages in this part that chase or remind someone
   about this case (e.g. "any update?", "approval pending", "jaldi batao")

Only use message numbers that appear in this part. Ignore chat noise.
Return JSON: {"cases": [ ... ]}"""


def _steps_text(pmap: ProcessMap) -> str:
    return "Process steps:\n" + "\n".join(
        f"  {s.id}. {s.name} (done by {s.actor})" for s in pmap.steps)


def _open_cases_text(cases: dict, hints: dict, names: dict) -> str:
    if not cases:
        return "Open cases: none yet."
    lines = ["Open cases from earlier parts:"]
    for cid, c in cases.items():
        done = ", ".join(dict.fromkeys(names.get(e.step_id, e.step_id) for e in c.events))
        lines.append(f"  {cid}: {c.label} | details: {hints.get(cid, '')} | done so far: {done}")
    return "\n".join(lines)


def _chunk_text(digest: ChatDigest, start: int, end: int) -> str:
    lines = [f"Messages #{start} to #{end - 1}:"]
    for i in range(start, end):
        m = digest.messages[i]
        lines.append(f"#{i} [{m.timestamp:%a %d %b %H:%M}] {m.sender}: {m.text.replace(chr(10), ' / ')}")
    return "\n".join(lines)


def extract_event_log(pmap: ProcessMap, digest: ChatDigest,
                      progress=lambda msg: None) -> EventLog:
    msgs = digest.messages
    valid_steps = {s.id for s in pmap.steps}
    names = {s.id: s.name for s in pmap.steps}
    cases: dict[str, Case] = {}
    hints: dict[str, str] = {}

    chunks = list(range(0, len(msgs), CHUNK_SIZE))
    for n, start in enumerate(chunks, 1):
        end = min(start + CHUNK_SIZE, len(msgs))
        if len(chunks) > 1:
            progress(f"      reading messages {start}-{end - 1} ({n}/{len(chunks)})")
        prompt = "\n\n".join([INSTRUCTIONS, _steps_text(pmap),
                              _open_cases_text(cases, hints, names), _chunk_text(digest, start, end)])
        result = generate_json(SYSTEM, prompt, ChunkResult)

        for cc in result.cases:
            events = [e for e in cc.events
                      if e.step_id in valid_steps and start <= e.message_id < end]
            chasers = [i for i in cc.chaser_message_ids if start <= i < end]
            if not events and not chasers:
                continue
            if cc.case_id in cases:
                case = cases[cc.case_id]
            else:
                cid = f"C{len(cases) + 1}"
                case = Case(case_id=cid, label=cc.label or f"Case {len(cases) + 1}")
                cases[cid] = case
                hints[cid] = cc.hint
            # Guard against mixing up repeat customers: a case that already has
            # this step should not get it again weeks later.
            for e in events:
                t = msgs[e.message_id].timestamp
                same = [msgs[x.message_id].timestamp for x in case.events if x.step_id == e.step_id]
                if all(abs((t - x).days) <= MAX_REPEAT_DAYS for x in same):
                    case.events.append(e)
            case.chaser_message_ids += chasers

    # Clean up: one event per message, in time order; drop cases with a single event
    for case in cases.values():
        seen, clean = set(), []
        for e in sorted(case.events, key=lambda e: msgs[e.message_id].timestamp):
            if e.message_id not in seen:
                seen.add(e.message_id)
                clean.append(e)
        case.events = clean
        case.chaser_message_ids = sorted(set(case.chaser_message_ids))
    # Labels are generated in code so no customer names can leak into reports
    kept = sorted((c for c in cases.values() if len(c.events) >= 2),
                  key=lambda c: msgs[c.events[0].message_id].timestamp)
    for k, c in enumerate(kept, 1):
        c.case_id = f"C{k}"
        c.label = f"Case {k} (started {msgs[c.events[0].message_id].timestamp:%d %b})"
    return EventLog(cases=kept)

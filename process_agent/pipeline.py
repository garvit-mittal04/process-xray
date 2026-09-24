"""Runs the full pipeline and returns one Report object.

The same Report is printed by xray.py, saved as JSON, and (later) loaded by the
Streamlit app, so a saved report can power an instant demo with no AI calls."""
from datetime import datetime
from pathlib import Path
from typing import Callable

from pydantic import BaseModel

from .analyst import Analysis, analyse
from .chat_parser import build_digest, parse_whatsapp
from .critic import RankedRecommendation, critique
from .events import EventLog, extract_event_log
from .mapper import ProcessMap, map_process
from .measure import measure_steps
from .recommender import Plan, Settings, recommend, value
from .replay import ReplayResult, replay


class ChatSummary(BaseModel):
    source_name: str
    total_messages: int
    start: datetime
    end: datetime
    participants: dict[str, int]
    follow_up_count: int
    attachment_count: int


class Report(BaseModel):
    chat: ChatSummary
    process_map: ProcessMap
    analysis: Analysis
    event_log: EventLog
    plan: Plan
    ranking: list[RankedRecommendation]
    replay: ReplayResult          # the history replayed with every recommended automation


# Safety net in case the Mapper forgets to flag a step: waits before these
# depend on customers, transporters or banks, so no automation may shorten them.
OUTSIDE_WORDS = ("payment", "paid", "dispatch", "transport", "deliver", "courier", "collect")


def _blocked_steps(pmap, analysis) -> set[str]:
    human = {s.id for s in analysis.steps if s.treatment == "keep_human"}
    return human | {
        s.id for s in pmap.steps
        if s.starts_on_external_event or s.timing_depends_on_outsiders
        or any(w in s.name.lower() for w in OUTSIDE_WORDS)
    }


def _rules(recs) -> dict[str, float]:
    rules: dict[str, float] = {}
    for r in recs:
        for x in r.replay_rules:
            rules[x.step_id] = min(x.max_wait_hours, rules.get(x.step_id, x.max_wait_hours))
    return rules


def run(chat_path: str | Path, day_first: bool = True, settings: Settings | None = None,
        progress: Callable[[str], None] = print) -> Report:
    name = Path(chat_path).stem
    digest = build_digest(parse_whatsapp(chat_path, day_first=day_first), name)

    progress("[1/5] Mapper agent is reconstructing the process...")
    pmap = measure_steps(map_process(digest), digest)
    progress("[2/5] Analyst agent is scoring every step...")
    analysis = analyse(pmap)
    progress("[3/5] Event-log agent is tracing every order through the chat...")
    log = extract_event_log(pmap, digest, progress)
    progress("[4/5] Recommender agent is designing automations and replaying history...")
    plan = recommend(analysis, settings)
    blocked = _blocked_steps(pmap, analysis)
    for r in plan.recommendations:
        r.replay_rules = [x for x in r.replay_rules if x.step_id not in blocked]
    per_rec = {r.id: replay(log, digest, pmap, _rules([r])) for r in plan.recommendations}
    plan = value(plan, analysis, pmap.roles, per_rec)
    progress("[5/5] Devil's advocate is stress-testing each idea...")
    ranking = critique(plan)

    keep = {x.recommendation_id for x in ranking if x.critique.verdict != "rethink"}
    combined = replay(log, digest, pmap, _rules(r for r in plan.recommendations if r.id in keep))

    return Report(
        chat=ChatSummary(
            source_name=name, total_messages=digest.total_messages,
            start=digest.date_range[0], end=digest.date_range[1],
            participants=digest.participants, follow_up_count=digest.follow_up_count,
            attachment_count=digest.attachment_count,
        ),
        process_map=pmap, analysis=analysis, event_log=log, plan=plan,
        ranking=ranking, replay=combined,
    )

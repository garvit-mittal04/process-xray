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
from .mapper import ProcessMap, map_process
from .measure import measure_steps
from .recommender import Plan, Settings, recommend


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
    plan: Plan
    ranking: list[RankedRecommendation]


def run(chat_path: str | Path, day_first: bool = True, settings: Settings | None = None,
        progress: Callable[[str], None] = print) -> Report:
    name = Path(chat_path).stem
    digest = build_digest(parse_whatsapp(chat_path, day_first=day_first), name)

    progress("[1/4] Mapper agent is reconstructing the process...")
    pmap = measure_steps(map_process(digest), digest)
    progress("[2/4] Analyst agent is scoring every step...")
    analysis = analyse(pmap)
    progress("[3/4] Recommender agent is designing automations...")
    plan = recommend(analysis, settings)
    progress("[4/4] Devil's advocate is stress-testing each idea...")
    ranking = critique(plan)

    return Report(
        chat=ChatSummary(
            source_name=name, total_messages=digest.total_messages,
            start=digest.date_range[0], end=digest.date_range[1],
            participants=digest.participants, follow_up_count=digest.follow_up_count,
            attachment_count=digest.attachment_count,
        ),
        process_map=pmap, analysis=analysis, plan=plan, ranking=ranking,
    )

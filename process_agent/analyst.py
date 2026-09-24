"""Analyst agent: scores every step and decides how to treat it.

The model judges qualities that need understanding (how rule-based is this?).
Code computes everything numeric (bottleneck score, hours, potential), so
numbers are consistent and explainable."""
from typing import Literal

from pydantic import BaseModel, Field

from .llm import generate_json
from .mapper import ProcessMap

Treatment = Literal[
    "eliminate", "simplify", "integrate", "automate_rules", "automate_ai", "keep_human"
]


class StepJudgement(BaseModel):
    id: str
    repetitiveness: int = Field(ge=1, le=5)
    rule_clarity: int = Field(ge=1, le=5)
    data_structure: int = Field(ge=1, le=5)
    error_risk: int = Field(ge=1, le=5)
    treatment: Treatment
    reason: str
    idea: str


class Judgements(BaseModel):
    steps: list[StepJudgement]
    top_insight: str


class ScoredStep(BaseModel):
    id: str
    name: str
    actor: str
    system: str
    frequency_per_week: float
    minutes_per_run: float
    wait_before_hours: float
    hands_on_hours_per_week: float
    waiting_hours_per_week: float
    bottleneck: int
    repetitiveness: int
    rule_clarity: int
    data_structure: int
    error_risk: int
    potential: float                    # 1-5, higher = better automation candidate
    treatment: Treatment
    reason: str
    idea: str
    message_ids: list[int]


class Analysis(BaseModel):
    process_name: str
    top_insight: str
    steps: list[ScoredStep]
    total_hands_on_hours_per_week: float
    total_waiting_hours_per_week: float


SYSTEM = """You are a senior process-automation consultant for small Indian
businesses (traders, distributors, manufacturers). You know their real tools:
WhatsApp and WhatsApp Business API, Tally, Excel/Google Sheets, email, phone,
UPI/bank payments, and no-code automation (n8n, Zapier, Make). You are practical
and honest: you never automate a step that needs human judgement or trust, and
you prefer removing waste over automating it. Reply with JSON only."""

RUBRIC = """Score every step from 1 to 5 on:
- repetitiveness: 1 = different every time, 5 = identical every time
- rule_clarity: 1 = needs judgement or negotiation, 5 = fully rule-based
- data_structure: 1 = free conversation or physical work, 5 = structured data already in a system
- error_risk: 1 = rarely goes wrong, 5 = errors or rework seen or very likely

Then choose ONE treatment:
- eliminate: the step exists only to relay, remind, or chase; fix the cause and it disappears
- simplify: keep it but remove waiting or approvals (e.g. auto-approve below a limit)
- integrate: connect two systems so data flows without retyping
- automate_rules: a clear rule a workflow tool can run (reminders, notifications, stock lookups)
- automate_ai: needs reading or writing language (extract PO details, draft messages)
- keep_human: judgement, relationships, or physical work that should stay human

For each step give "reason" (one sentence, grounded in this chat) and "idea"
(one concrete, affordable sentence for a small Indian business).

Return JSON: {"steps": [{"id", "repetitiveness", "rule_clarity", "data_structure",
"error_risk", "treatment", "reason", "idea"}, ...], "top_insight": "2 sentences
on the single biggest opportunity"}. Include every step id exactly once."""


def _bottleneck_score(wait_hours: float) -> int:
    for limit, score in ((1, 1), (4, 2), (8, 3), (20, 4)):
        if wait_hours < limit:
            return score
    return 5


def _format_steps(pmap: ProcessMap) -> str:
    lines = [f"Process: {pmap.process_name}", pmap.summary, "", "Roles:"]
    lines += [f"  {p}: {r}" for p, r in pmap.roles.items()]
    lines += ["", "Steps (frequency and waits measured from timestamps):"]
    for s in pmap.steps:
        lines.append(
            f"  {s.id}. {s.name} | {s.actor} via {s.system} | in: {s.input} | out: {s.output}"
            f" | {s.frequency_per_week:g}/week, ~{s.minutes_per_run:g} min,"
            f" waits {s.wait_before_hours:g} working h before starting")
    lines += ["", "Pain points found:"]
    lines += [f"  [{p.kind}] {p.description} (steps {', '.join(p.step_ids)})"
              for p in pmap.pain_points]
    return "\n".join(lines)


def analyse(pmap: ProcessMap) -> Analysis:
    judged = generate_json(SYSTEM, RUBRIC + "\n\n" + _format_steps(pmap), Judgements)
    by_id = {j.id: j for j in judged.steps}

    scored = []
    for s in pmap.steps:
        j = by_id.get(s.id) or StepJudgement(
            id=s.id, repetitiveness=1, rule_clarity=1, data_structure=1, error_risk=1,
            treatment="keep_human", reason="Not scored by the model.", idea="-")
        bottleneck = _bottleneck_score(s.wait_before_hours)
        potential = round(
            (j.repetitiveness + j.rule_clarity + j.data_structure + j.error_risk + bottleneck) / 5, 1)
        scored.append(ScoredStep(
            id=s.id, name=s.name, actor=s.actor, system=s.system,
            frequency_per_week=s.frequency_per_week, minutes_per_run=s.minutes_per_run,
            wait_before_hours=s.wait_before_hours,
            hands_on_hours_per_week=round(s.frequency_per_week * s.minutes_per_run / 60, 2),
            waiting_hours_per_week=round(s.frequency_per_week * s.wait_before_hours, 1),
            bottleneck=bottleneck, repetitiveness=j.repetitiveness,
            rule_clarity=j.rule_clarity, data_structure=j.data_structure,
            error_risk=j.error_risk, potential=potential, treatment=j.treatment,
            reason=j.reason, idea=j.idea, message_ids=s.message_ids,
        ))

    return Analysis(
        process_name=pmap.process_name,
        top_insight=judged.top_insight,
        steps=scored,
        total_hands_on_hours_per_week=round(sum(x.hands_on_hours_per_week for x in scored), 1),
        total_waiting_hours_per_week=round(sum(x.waiting_hours_per_week for x in scored), 1),
    )

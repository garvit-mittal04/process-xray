"""Recommender agent: turns scored steps into complete, buildable automations.

The model designs the automations and states how each one changes timing
(replay rules). Code then measures the effect by replaying the real history
and turns it into hours and rupees, using the assumptions in Settings."""
import re
from typing import Literal

from pydantic import BaseModel, Field

from .analyst import Analysis
from .llm import generate_json


class Settings(BaseModel):
    staff_cost_per_hour_inr: float = 150      # roughly a Rs 25-30k/month office salary
    owner_cost_per_hour_inr: float = 500      # the owner's time is the scarcest resource
    setup_cost_per_day_inr: float = 3000      # freelancer or in-house builder day rate
    minutes_per_chaser: float = 5             # writing, reading and acting on a reminder
    weeks_per_month: float = 4.33


class ReplayRule(BaseModel):
    step_id: str
    max_wait_hours: float = Field(ge=0)       # with the automation, this step starts within this many working hours
    why: str = ""


class Recommendation(BaseModel):
    id: str                                   # "R1", "R2", ...
    title: str
    step_ids: list[str]
    treatment: Literal["eliminate", "simplify", "integrate", "automate_rules", "automate_ai"]
    what_it_does: str
    how_it_works: list[str]                   # 3-5 build steps
    tools: list[str]
    effort_removed: float = Field(ge=0, le=1)   # share of hands-on minutes removed
    setup_days: float = Field(ge=0)
    monthly_tool_cost_inr: float = Field(ge=0)
    human_in_the_loop: str
    replay_rules: list[ReplayRule] = Field(default_factory=list)
    # Filled in by code
    hours_saved_per_week: float = 0.0
    waiting_removed_per_week: float = 0.0
    chasers_avoided_per_week: float = 0.0
    monthly_value_inr: float = 0.0
    setup_cost_inr: float = 0.0
    payback_months: float | None = None


class Plan(BaseModel):
    recommendations: list[Recommendation]
    settings: Settings = Field(default_factory=Settings)


SYSTEM = """You are a senior automation consultant for small Indian businesses.
You design automations they can realistically build and afford: WhatsApp
Business API (via providers like Interakt, AiSensy, Wati), Tally (TDL or
connectors), Google Sheets, Google Forms, email, and no-code tools (n8n,
Make, Zapier), plus AI for reading documents or drafting messages. You are
conservative with estimates. Reply with JSON only."""

INSTRUCTIONS = """Design 3 to 5 automations from this analysis. Group related
steps into one complete automation when they belong to the same flow, rather
than one idea per step. Prefer eliminating or simplifying steps over automating
them. Never automate a step marked keep_human, but you may support that person
(e.g. give them information faster).

Return JSON: {"recommendations": [ {
  "id": "R1", "title": short name,
  "step_ids": steps it covers,
  "treatment": one of eliminate, simplify, integrate, automate_rules, automate_ai,
  "what_it_does": 2 sentences in plain language for a business owner,
  "how_it_works": 3-5 short build steps,
  "tools": specific tools,
  "effort_removed": 0-1, share of hands-on minutes on those steps it removes (be conservative),
  "setup_days": realistic days for one person to build and test,
  "monthly_tool_cost_inr": realistic monthly cost in rupees (0 if free tiers suffice),
  "human_in_the_loop": where a person still checks or decides,
  "replay_rules": how it changes timing, as a list of {"step_id", "max_wait_hours", "why"}.
      For each covered step that would start sooner, give the most working hours the
      wait before it would take with this automation (e.g. a live stock sheet means
      stock is confirmed within 0.5 h; auto-approval below a limit means approval
      within 0.2 h). Only include steps whose timing the automation truly controls;
      never for steps that depend on customers, transporters or physical work.
} ]}"""


def _format(analysis: Analysis) -> str:
    lines = [f"Process: {analysis.process_name}", f"Insight: {analysis.top_insight}", "", "Steps:"]
    for s in analysis.steps:
        lines.append(
            f"  {s.id}. {s.name} | {s.actor} via {s.system} | treatment {s.treatment} "
            f"| potential {s.potential}/5 | {s.hands_on_hours_per_week} h/week hands-on | "
            f"waits {s.wait_before_hours} working h before starting | idea: {s.idea}")
    return "\n".join(lines)


def _is_owner(actor: str, roles: dict[str, str]) -> bool:
    people = re.findall(r"Person \d+", actor)
    text = " ".join([actor] + [roles.get(p, "") for p in people]).lower()
    return any(w in text for w in ("owner", "manager", "proprietor", "director", "malik"))


def recommend(analysis: Analysis, settings: Settings | None = None) -> Plan:
    plan = generate_json(SYSTEM, INSTRUCTIONS + "\n\n" + _format(analysis), Plan)
    plan.settings = settings or Settings()
    valid = {s.id for s in analysis.steps}
    for r in plan.recommendations:
        r.step_ids = [i for i in r.step_ids if i in valid]
        r.replay_rules = [x for x in r.replay_rules if x.step_id in valid]
    return plan


def value(plan: Plan, analysis: Analysis, roles: dict[str, str], replays: dict | None = None) -> Plan:
    """Turn effort and replay results into hours and rupees per recommendation."""
    s = plan.settings
    steps = {x.id: x for x in analysis.steps}
    for r in plan.recommendations:
        covered = [steps[i] for i in r.step_ids if steps[i].treatment != "keep_human"]
        rupees_per_week = 0.0
        hours = 0.0
        for st in covered:
            h = st.hands_on_hours_per_week * r.effort_removed
            rate = s.owner_cost_per_hour_inr if _is_owner(st.actor, roles) else s.staff_cost_per_hour_inr
            hours += h
            rupees_per_week += h * rate
        rp = (replays or {}).get(r.id)
        if rp:
            r.waiting_removed_per_week = rp.waiting_removed_per_week
            r.chasers_avoided_per_week = rp.chasers_avoided_per_week
            chaser_hours = rp.chasers_avoided_per_week * s.minutes_per_chaser / 60
            hours += chaser_hours
            rupees_per_week += chaser_hours * s.staff_cost_per_hour_inr
        r.hours_saved_per_week = round(hours, 2)
        r.monthly_value_inr = round(rupees_per_week * s.weeks_per_month - r.monthly_tool_cost_inr)
        r.setup_cost_inr = round(r.setup_days * s.setup_cost_per_day_inr)
        r.payback_months = (round(r.setup_cost_inr / r.monthly_value_inr, 1)
                            if r.monthly_value_inr > 0 else None)
    return plan

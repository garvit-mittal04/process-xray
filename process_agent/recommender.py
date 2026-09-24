"""Recommender agent: turns scored steps into complete, buildable automations.

The model designs the automations and estimates what fraction of effort and
waiting each removes. Code turns that into hours and rupees, using the
assumptions in Settings so every number in the report can be explained."""
from typing import Literal

from pydantic import BaseModel, Field

from .analyst import Analysis
from .llm import generate_json


class Settings(BaseModel):
    staff_cost_per_hour_inr: float = 150      # roughly a Rs 25-30k/month office salary
    setup_cost_per_day_inr: float = 3000      # freelancer or in-house builder day rate
    weeks_per_month: float = 4.33


class Recommendation(BaseModel):
    id: str                                   # "R1", "R2", ...
    title: str
    step_ids: list[str]
    treatment: Literal["eliminate", "simplify", "integrate", "automate_rules", "automate_ai"]
    what_it_does: str
    how_it_works: list[str]                   # 3-5 build steps
    tools: list[str]
    effort_removed: float = Field(ge=0, le=1)   # share of hands-on minutes removed
    waiting_removed: float = Field(ge=0, le=1)  # share of waiting removed
    setup_days: float = Field(ge=0)
    monthly_tool_cost_inr: float = Field(ge=0)
    human_in_the_loop: str
    # Filled in by code
    hours_saved_per_week: float = 0.0
    waiting_removed_per_week: float = 0.0
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
steps into one complete automation when they belong to the same flow (e.g. an
order-to-invoice flow), rather than one idea per step. Prefer eliminating or
simplifying steps over automating them. Never cover a step marked keep_human,
except to support the human (e.g. give them information faster).

Return JSON: {"recommendations": [ {
  "id": "R1", "title": short name,
  "step_ids": steps it covers,
  "treatment": one of eliminate, simplify, integrate, automate_rules, automate_ai,
  "what_it_does": 2 sentences in plain language for a business owner,
  "how_it_works": 3-5 short build steps,
  "tools": specific tools,
  "effort_removed": 0-1, share of hands-on minutes on those steps it removes (be conservative),
  "waiting_removed": 0-1, share of waiting on those steps it removes (be conservative),
  "setup_days": realistic days for one person to build and test,
  "monthly_tool_cost_inr": realistic monthly cost in rupees (0 if free tiers suffice),
  "human_in_the_loop": where a person still checks or decides
} ]}"""


def _format(analysis: Analysis) -> str:
    lines = [f"Process: {analysis.process_name}", f"Insight: {analysis.top_insight}", "", "Steps:"]
    for s in analysis.steps:
        lines.append(
            f"  {s.id}. {s.name} | {s.actor} via {s.system} | treatment {s.treatment} "
            f"| potential {s.potential}/5 | {s.hands_on_hours_per_week} h/week hands-on, "
            f"{s.waiting_hours_per_week} h/week waiting | idea: {s.idea}")
    return "\n".join(lines)


def recommend(analysis: Analysis, settings: Settings | None = None) -> Plan:
    settings = settings or Settings()
    plan = generate_json(SYSTEM, INSTRUCTIONS + "\n\n" + _format(analysis), Plan)
    plan.settings = settings
    steps = {s.id: s for s in analysis.steps}

    for r in plan.recommendations:
        r.step_ids = [i for i in r.step_ids if i in steps]
        # Only count steps that exist and are not meant to stay human
        covered = [steps[i] for i in r.step_ids if i in steps and steps[i].treatment != "keep_human"]
        hands_on = sum(s.hands_on_hours_per_week for s in covered)
        waiting = sum(s.waiting_hours_per_week for s in covered)
        r.hours_saved_per_week = round(hands_on * r.effort_removed, 2)
        r.waiting_removed_per_week = round(waiting * r.waiting_removed, 1)
        gross = r.hours_saved_per_week * settings.weeks_per_month * settings.staff_cost_per_hour_inr
        r.monthly_value_inr = round(gross - r.monthly_tool_cost_inr)
        r.setup_cost_inr = round(r.setup_days * settings.setup_cost_per_day_inr)
        r.payback_months = (round(r.setup_cost_inr / r.monthly_value_inr, 1)
                            if r.monthly_value_inr > 0 else None)
    return plan

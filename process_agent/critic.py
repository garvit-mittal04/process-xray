"""Devil's advocate agent: attacks every recommendation before the owner sees it.

It looks for what breaks, who resists, and what could go wrong, then code
ranks recommendations by value adjusted for that risk."""
from typing import Literal

from pydantic import BaseModel, Field

from .llm import generate_json
from .recommender import Plan


class Risk(BaseModel):
    risk: str
    severity: int = Field(ge=1, le=5)
    mitigation: str


class Critique(BaseModel):
    id: str                                    # matches Recommendation.id
    risks: list[Risk]
    who_might_resist: str
    confidence: int = Field(ge=1, le=5)        # will it work as described?
    verdict: Literal["go", "go_with_changes", "rethink"]
    change_needed: str = ""


class Critiques(BaseModel):
    critiques: list[Critique]


class RankedRecommendation(BaseModel):
    rank: int
    recommendation_id: str
    priority_score: float
    critique: Critique


SYSTEM = """You are a sceptical operations expert reviewing automation proposals
for a small Indian business. Your job is to protect the owner from ideas that
look good on paper but fail in practice: scanned or handwritten documents,
Hinglish messages, customers who negotiate rates, unreliable internet, staff
who fear for their jobs, festival-season spikes, GST and compliance, and tools
that are harder to set up than claimed. Be specific to this business, fair,
and constructive. Reply with JSON only."""

INSTRUCTIONS = """Review every recommendation below. For each, return:
- "id": the recommendation id
- "risks": 2-3 specific risks, each with "risk", "severity" (1-5) and a practical "mitigation"
- "who_might_resist": which role might push back and why, in one sentence
- "confidence": 1-5, how likely it works as described for this business
- "verdict": "go", "go_with_changes", or "rethink"
- "change_needed": if not "go", the one change that would fix it

Return JSON: {"critiques": [ ... ]} with every recommendation id exactly once."""

VERDICT_WEIGHT = {"go": 1.0, "go_with_changes": 0.8, "rethink": 0.3}
WAITING_WEIGHT = 0.25     # an hour of waiting removed counts as a quarter of an hour of work saved


def _format(plan: Plan) -> str:
    lines = []
    for r in plan.recommendations:
        lines += [
            f"{r.id}. {r.title} (covers {', '.join(r.step_ids)}; {r.treatment})",
            f"   What: {r.what_it_does}",
            f"   How: {' | '.join(r.how_it_works)}",
            f"   Tools: {', '.join(r.tools)} | setup {r.setup_days:g} days | "
            f"Rs {r.monthly_tool_cost_inr:,.0f}/month",
            f"   Human in the loop: {r.human_in_the_loop}",
            f"   Expected: saves {r.hours_saved_per_week} h/week of work and "
            f"{r.waiting_removed_per_week} h/week of waiting",
        ]
    return "\n".join(lines)


def critique(plan: Plan) -> list[RankedRecommendation]:
    result = generate_json(SYSTEM, INSTRUCTIONS + "\n\n" + _format(plan), Critiques)
    by_id = {c.id: c for c in result.critiques}

    ranked = []
    for r in plan.recommendations:
        c = by_id.get(r.id) or Critique(
            id=r.id, risks=[], who_might_resist="Not reviewed.", confidence=2,
            verdict="go_with_changes", change_needed="Review manually before building.")
        value = r.hours_saved_per_week + WAITING_WEIGHT * r.waiting_removed_per_week
        score = value * (c.confidence / 5) * VERDICT_WEIGHT[c.verdict]
        ranked.append(RankedRecommendation(
            rank=0, recommendation_id=r.id, priority_score=round(score, 2), critique=c))

    ranked.sort(key=lambda x: x.priority_score, reverse=True)
    for n, item in enumerate(ranked, 1):
        item.rank = n
    return ranked

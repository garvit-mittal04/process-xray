"""Known-answer test: runs the whole pipeline on the generated two-month chat,
with the AI agents replaced by replies built from the generator's ground truth.

It checks the deterministic parts (parsing, masking, chunk stitching,
measurement, guardrails and the replay engine) without any API calls.

Usage: python3 tests/test_known_answer.py
"""
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
os.environ.setdefault("GEMINI_API_KEY", "not-used-in-this-test")

from generate_sample import generate  # noqa: E402
from process_agent import llm  # noqa: E402
from process_agent.pipeline import run  # noqa: E402

CHAT = ROOT / "sample_data" / "sharma_traders_2months.txt"
GEN = generate()

KEY = {  # generator event -> (step id, step name, who does it)
    "order": ("S1", "Receive order", "Person 1"),
    "stock_request": ("S2", "Ask godown for stock", "Person 2"),
    "stock_confirm": ("S3", "Confirm stock", "Person 3"),
    "partial_decision": ("S4", "Decide partial dispatch", "Person 1"),
    "invoice": ("S5", "Create invoice in Tally", "Person 2"),
    "approval": ("S6", "Approve invoice", "Person 1"),
    "invoice_fix": ("S7", "Correct invoice", "Person 2"),
    "dispatch_request": ("S8", "Request dispatch", "Person 2"),
    "dispatched": ("S9", "Dispatch goods and share LR", "Person 3"),
    "tally_update": ("S10", "Update Tally and inform customer", "Person 2"),
    "payment_chase": ("S11", "Chase payment", "Person 1"),
    "payment_received": ("S12", "Record payment received", "Person 2"),
}
STEPS = [{"id": sid, "name": name, "actor": who, "system": "WhatsApp", "input": "-", "output": "-",
          "minutes_per_run": 5, "starts_on_external_event": key == "order",
          "message_ids": [i for i, g in enumerate(GEN) if g[3] == key]}
         for key, (sid, name, who) in KEY.items()]
TREATMENTS = ["automate_ai", "eliminate", "integrate", "keep_human", "integrate", "simplify",
              "eliminate", "automate_rules", "keep_human", "integrate", "automate_rules", "integrate"]

REPLIES = {
    "map": {"process_name": "Order to cash", "summary": "Orders arrive on WhatsApp.",
            "roles": {"Person 1": "Owner", "Person 2": "Office staff", "Person 3": "Godown staff"},
            "steps": STEPS, "pain_points": [], "mermaid": "flowchart TD\n S1 --> S2"},
    "analyse": {"steps": [{"id": s["id"], "repetitiveness": 4, "rule_clarity": 4, "data_structure": 3,
                           "error_risk": 3, "treatment": t, "reason": "r", "idea": "i"}
                          for s, t in zip(STEPS, TREATMENTS)], "top_insight": "Waiting dominates."},
    # Reference automations. R2 also (wrongly) tries to speed up dispatch and payment:
    # the guardrails must ignore those rules, or the numbers below would be inflated.
    "recommend": {"recommendations": [
        {"id": "R1", "title": "Live stock sheet", "step_ids": ["S2", "S3"], "treatment": "integrate",
         "what_it_does": "w", "how_it_works": ["a"], "tools": ["Google Sheets"], "effort_removed": 0.7,
         "setup_days": 2, "monthly_tool_cost_inr": 0, "human_in_the_loop": "h",
         "replay_rules": [{"step_id": "S3", "max_wait_hours": 0.5, "why": "live sheet"}]},
        {"id": "R2", "title": "Auto-approve under a limit", "step_ids": ["S6", "S7"], "treatment": "simplify",
         "what_it_does": "w", "how_it_works": ["a"], "tools": ["Tally"], "effort_removed": 0.6,
         "setup_days": 3, "monthly_tool_cost_inr": 0, "human_in_the_loop": "h",
         "replay_rules": [{"step_id": "S6", "max_wait_hours": 0.3, "why": "rule"},
                          {"step_id": "S9", "max_wait_hours": 0.2, "why": "should be blocked"},
                          {"step_id": "S12", "max_wait_hours": 0.1, "why": "should be blocked"}]},
        {"id": "R3", "title": "AI reads POs", "step_ids": ["S1"], "treatment": "automate_ai",
         "what_it_does": "w", "how_it_works": ["a"], "tools": ["AI"], "effort_removed": 0.5,
         "setup_days": 10, "monthly_tool_cost_inr": 3000, "human_in_the_loop": "h",
         "replay_rules": [{"step_id": "S2", "max_wait_hours": 0.05, "why": "auto"}]}]},
    "critique": {"critiques": [
        {"id": "R1", "risks": [], "who_might_resist": "x", "confidence": 4, "verdict": "go"},
        {"id": "R2", "risks": [], "who_might_resist": "x", "confidence": 4, "verdict": "go_with_changes"},
        {"id": "R3", "risks": [], "who_might_resist": "x", "confidence": 2, "verdict": "rethink"}]},
}


def chunk_reply(prompt: str) -> str:
    """Simulate the event-log agent for one chunk, reusing ids of open cases."""
    a, b = map(int, re.search(r"Messages #(\d+) to #(\d+):", prompt).groups())
    known = {int(o): cid for cid, o in re.findall(r"  (C\d+): .*?details: order=(\d+)", prompt)}
    cases = {}
    for i in range(a, b + 1):
        _, _, _, key, order = GEN[i]
        if order is None:
            continue
        c = cases.setdefault(order, {"case_id": known.get(order, "NEW"), "label": f"order {order}",
                                     "hint": f"order={order}", "events": [], "chaser_message_ids": []})
        if key == "chaser":
            c["chaser_message_ids"].append(i)
        elif key in KEY:
            c["events"].append({"step_id": KEY[key][0], "message_id": i})
    return json.dumps({"cases": list(cases.values())})


def fake_model(model, system, prompt):
    if "You are reading one part" in prompt:
        return chunk_reply(prompt)
    if "Review every recommendation" in prompt:
        return json.dumps(REPLIES["critique"])
    if "Design 3 to 5" in prompt:
        return json.dumps(REPLIES["recommend"])
    if "Score every step" in prompt:
        return json.dumps(REPLIES["analyse"])
    return json.dumps(REPLIES["map"])


def main() -> None:
    llm._call_model = fake_model
    report = run(CHAT, progress=lambda _msg: None)
    rp = report.replay
    dispatch = next(m for m in rp.milestones if m.step_id == "S9")
    payment = next(m for m in rp.milestones if m.step_id == "S12")

    checks = {
        "all 32 orders traced across chunks": rp.cases == 32,
        "29 of 32 reminders avoided": rp.chasers_avoided == 29,
        "about 30.7 h/week of waiting removed": abs(rp.waiting_removed_per_week - 30.7) < 0.5,
        "dispatch reached about 7.7 h sooner": abs(dispatch.avg_hours_before - dispatch.avg_hours_after - 7.7) < 0.3,
        "guardrail: payment gains only a knock-on effect":
            payment.avg_hours_before - payment.avg_hours_after < 10,
        "guardrail: blocked rules removed": all(x.step_id not in ("S9", "S12")
                                                for r in report.plan.recommendations for x in r.replay_rules),
        "no customer names reach the report": not any(n in report.model_dump_json()
                                                      for n in ("Mehta", "Gupta", "Singh", "Arora")),
        "case labels generated by code": all(c.label.startswith("Case ") for c in report.event_log.cases),
    }
    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    if not all(checks.values()):
        sys.exit(1)
    print("\nAll checks passed.")


if __name__ == "__main__":
    main()

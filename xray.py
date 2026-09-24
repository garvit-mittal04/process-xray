"""Process X-Ray: analyse a WhatsApp chat export.

Usage:
  python3 xray.py sample_data/sharma_traders_orders.txt           # full analysis
  python3 xray.py sample_data/sharma_traders_orders.txt --parse   # parser only, no AI
Options: --us-dates (MM/DD dates), --staff-cost 150 (Rs per hour)
"""
import argparse
from pathlib import Path

from process_agent.chat_parser import build_digest, parse_whatsapp

LABELS = {
    "eliminate": "ELIMINATE", "simplify": "SIMPLIFY", "integrate": "INTEGRATE",
    "automate_rules": "AUTOMATE (rules)", "automate_ai": "AUTOMATE (AI)",
    "keep_human": "KEEP HUMAN",
}
VERDICTS = {"go": "GO", "go_with_changes": "GO WITH CHANGES", "rethink": "RETHINK"}


def show_report(r) -> None:
    pm, an = r.process_map, r.analysis
    print(f"\n=== {pm.process_name} ===\n{pm.summary}")
    print("Roles: " + ", ".join(f"{p} = {role}" for p, role in pm.roles.items()))

    print("\nSTEPS  (waits = measured working hours from real timestamps)")
    for s in an.steps:
        print(f"  {s.id}. {s.name} [{LABELS[s.treatment]}] potential {s.potential}/5 | "
              f"{s.frequency_per_week:g}/week, ~{s.minutes_per_run:g} min, waits {s.wait_before_hours:g} h")
    print(f"\n  Hands-on work: {an.total_hands_on_hours_per_week} h/week | "
          f"Waiting: {an.total_waiting_hours_per_week} h/week")
    print(f"  Insight: {an.top_insight}")

    recs = {x.id: x for x in r.plan.recommendations}
    print("\nRECOMMENDED AUTOMATIONS  (ranked by value, adjusted for risk)")
    for item in r.ranking:
        rec, c = recs[item.recommendation_id], item.critique
        payback = f"{rec.payback_months:g} months" if rec.payback_months else "n/a"
        print(f"\n  #{item.rank} {rec.title}  [{VERDICTS[c.verdict]}, confidence {c.confidence}/5]")
        print(f"     Covers {', '.join(rec.step_ids)} | {LABELS[rec.treatment]}")
        print(f"     {rec.what_it_does}")
        print(f"     Saves {rec.hours_saved_per_week} h/week of work, {rec.waiting_removed_per_week} h/week "
              f"of waiting | value Rs {rec.monthly_value_inr:,.0f}/month")
        print(f"     Setup {rec.setup_days:g} days (Rs {rec.setup_cost_inr:,.0f}) | "
              f"tools Rs {rec.monthly_tool_cost_inr:,.0f}/month | payback {payback}")
        print(f"     Tools: {', '.join(rec.tools)}")
        print(f"     Human stays in: {rec.human_in_the_loop}")
        for risk in c.risks:
            print(f"     Risk ({risk.severity}/5): {risk.risk} -> {risk.mitigation}")
        print(f"     Who may resist: {c.who_might_resist}")
        if c.change_needed:
            print(f"     Change needed: {c.change_needed}")

    s = r.plan.settings
    print(f"\nAssumptions: staff time Rs {s.staff_cost_per_hour_inr:g}/h, builder Rs "
          f"{s.setup_cost_per_day_inr:,.0f}/day. Minutes per step are estimates; "
          f"frequencies and waits are measured.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Process X-Ray")
    ap.add_argument("chat", help="Path to exported WhatsApp .txt file")
    ap.add_argument("--parse", action="store_true", help="Run the parser only (no AI)")
    ap.add_argument("--us-dates", action="store_true", help="Dates are MM/DD")
    ap.add_argument("--staff-cost", type=float, default=150, help="Staff cost in Rs per hour")
    args = ap.parse_args()

    if args.parse:
        d = build_digest(parse_whatsapp(args.chat, day_first=not args.us_dates), Path(args.chat).stem)
        print(f"{d.total_messages} messages | follow-up chasers: {d.follow_up_count} | "
              f"files: {d.attachment_count}")
        for st in d.response_stats:
            print(f"  {st.responder}: median reply {st.median_minutes / 60:.1f} h")
        return

    # Imported here so --parse works without an API key
    from process_agent.pipeline import run
    from process_agent.recommender import Settings

    report = run(args.chat, day_first=not args.us_dates,
                 settings=Settings(staff_cost_per_hour_inr=args.staff_cost))
    show_report(report)

    out = Path("outputs")
    out.mkdir(exist_ok=True)
    path = out / f"{report.chat.source_name}_report.json"
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    (out / f"{report.chat.source_name}_map.mmd").write_text(report.process_map.mermaid, encoding="utf-8")
    print(f"\nFull report saved to {path}")


if __name__ == "__main__":
    main()

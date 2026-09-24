"""Process X-Ray: analyse a WhatsApp chat export.

Usage:
  python3 xray.py sample_data/sharma_traders_orders.txt           # full analysis
  python3 xray.py sample_data/sharma_traders_orders.txt --parse   # parser only, no AI
  add --us-dates if your phone shows dates as MM/DD
"""
import argparse
from pathlib import Path

from process_agent.chat_parser import build_digest, parse_whatsapp

LABELS = {
    "eliminate": "ELIMINATE", "simplify": "SIMPLIFY", "integrate": "INTEGRATE",
    "automate_rules": "AUTOMATE (rules)", "automate_ai": "AUTOMATE (AI)",
    "keep_human": "KEEP HUMAN",
}


def show_digest(d) -> None:
    start, end = d.date_range
    print(f"\n{d.source_name}: {d.total_messages} messages, "
          f"{start:%d %b %Y} to {end:%d %b %Y}")
    print(f"Follow-up chasers: {d.follow_up_count} | Files passed around: {d.attachment_count}")


def show_analysis(pmap, analysis) -> None:
    print(f"\n=== {pmap.process_name} ===\n{pmap.summary}\n")
    print("Roles: " + ", ".join(f"{p} = {r}" for p, r in pmap.roles.items()))

    print("\nStep-by-step (waits are measured working hours from real timestamps):")
    for s in analysis.steps:
        print(f"\n  {s.id}. {s.name}  [{LABELS[s.treatment]}]  potential {s.potential}/5")
        print(f"      {s.actor} via {s.system} | {s.frequency_per_week:g}/week | "
              f"~{s.minutes_per_run:g} min | waits {s.wait_before_hours:g} h")
        print(f"      Why: {s.reason}")
        if s.treatment != "keep_human":
            print(f"      Idea: {s.idea}")

    print(f"\nHands-on work: {analysis.total_hands_on_hours_per_week} h/week | "
          f"Time spent waiting: {analysis.total_waiting_hours_per_week} h/week")

    top = sorted((s for s in analysis.steps if s.treatment != "keep_human"),
                 key=lambda s: (s.potential, s.waiting_hours_per_week), reverse=True)[:3]
    print("\nTop 3 opportunities:")
    for n, s in enumerate(top, 1):
        print(f"  {n}. {s.name} ({LABELS[s.treatment]}): {s.idea}")
    print(f"\nBiggest insight: {analysis.top_insight}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Process X-Ray")
    ap.add_argument("chat", help="Path to exported WhatsApp .txt file")
    ap.add_argument("--parse", action="store_true", help="Run the parser only (no AI)")
    ap.add_argument("--us-dates", action="store_true", help="Dates are MM/DD")
    args = ap.parse_args()

    name = Path(args.chat).stem
    digest = build_digest(parse_whatsapp(args.chat, day_first=not args.us_dates), name)
    show_digest(digest)
    if args.parse:
        return

    # Imported here so --parse works without an API key
    from process_agent.analyst import analyse
    from process_agent.mapper import map_process
    from process_agent.measure import measure_steps

    print("\n[1/2] Mapper agent is reconstructing the process...")
    pmap = measure_steps(map_process(digest), digest)
    print("[2/2] Analyst agent is scoring every step...")
    analysis = analyse(pmap)
    show_analysis(pmap, analysis)

    out = Path("outputs")
    out.mkdir(exist_ok=True)
    (out / f"{name}_map.json").write_text(pmap.model_dump_json(indent=2), encoding="utf-8")
    (out / f"{name}_analysis.json").write_text(analysis.model_dump_json(indent=2), encoding="utf-8")
    (out / f"{name}_map.mmd").write_text(pmap.mermaid, encoding="utf-8")
    print(f"\nSaved results in outputs/")


if __name__ == "__main__":
    main()

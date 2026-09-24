"""Process X-Ray: analyse a WhatsApp chat export.

Usage:
  python3 xray.py sample_data/sharma_traders_orders.txt           # full analysis
  python3 xray.py sample_data/sharma_traders_orders.txt --parse   # parser only, no AI
  add --us-dates if your phone shows dates as MM/DD
"""
import argparse
from pathlib import Path

from process_agent.chat_parser import build_digest, parse_whatsapp


def show_digest(d) -> None:
    start, end = d.date_range
    print(f"\n{d.source_name}: {d.total_messages} messages, "
          f"{start:%d %b %Y} to {end:%d %b %Y}")
    print("\nWho keeps others waiting (median reply time):")
    for s in d.response_stats:
        print(f"  {s.responder}: {s.median_minutes / 60:.1f} h "
              f"(slowest {s.slowest_minutes / 60:.1f} h)")
    print(f"\nFollow-up chasers: {d.follow_up_count} | Files passed around: {d.attachment_count}")


def show_map(pmap) -> None:
    print(f"\n=== {pmap.process_name} ===\n{pmap.summary}\n\nRoles:")
    for person, role in pmap.roles.items():
        print(f"  {person}: {role}")
    print("\nSteps:")
    for s in pmap.steps:
        guessed = f"  (assumed: {', '.join(s.assumed_fields)})" if s.assumed_fields else ""
        print(f"  {s.id}. {s.name}")
        print(f"      {s.actor} via {s.system} | ~{s.frequency_per_week:g}/week, "
              f"{s.minutes_per_run:g} min, waits {s.wait_before_hours:g} h{guessed}")
    print("\nPain points:")
    for p in pmap.pain_points:
        print(f"  [{p.kind}] {p.description} (steps {', '.join(p.step_ids)})")


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

    from process_agent.mapper import map_process   # imported here so --parse needs no key
    print("\nAsking the Mapper agent (10-30 seconds)...")
    pmap = map_process(digest)
    show_map(pmap)

    out = Path("outputs")
    out.mkdir(exist_ok=True)
    (out / f"{name}_map.json").write_text(pmap.model_dump_json(indent=2), encoding="utf-8")
    (out / f"{name}_map.mmd").write_text(pmap.mermaid, encoding="utf-8")
    print(f"\nSaved to outputs/{name}_map.json")


if __name__ == "__main__":
    main()

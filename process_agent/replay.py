"""Replay engine: re-runs the real history as if the automations had existed.

For every case, it walks through the real events in order. Where an automation
applies to a step, the wait before that step is capped at the automation's
limit; every other wait stays exactly as it really was. Later events shift
earlier as a result. All waits are in working hours, and nothing is ever made
later than it really happened."""
from datetime import datetime

from pydantic import BaseModel

from .chat_parser import FOLLOW_UP
from .events import EventLog
from .mapper import ProcessMap
from .measure import add_working_hours, working_hours_between
from .schema import ChatDigest


class Moment(BaseModel):
    case_label: str
    step_name: str
    before: datetime
    after: datetime
    hours_sooner: float


class CaseResult(BaseModel):
    case_id: str
    label: str
    start: datetime
    end_before: datetime
    end_after: datetime
    working_hours_saved: float


class Milestone(BaseModel):
    step_id: str
    step_name: str
    cases: int
    avg_hours_before: float        # working hours from the start of the case
    avg_hours_after: float


class ReplayResult(BaseModel):
    rules: dict[str, float]
    cases: int
    avg_cycle_before_h: float
    avg_cycle_after_h: float
    avg_days_before: float
    avg_days_after: float
    waiting_removed_h_total: float
    waiting_removed_per_week: float
    chasers_avoided: int
    chasers_avoided_per_week: float
    moments: list[Moment]
    milestones: list[Milestone]    # how soon each step is reached, before vs after
    case_results: list[CaseResult]


def _simulate(times: list[datetime], steps: list[str],
              rules: dict[str, float]) -> tuple[list[datetime], set[int]]:
    """Returns the new times, and which events had their wait directly cut."""
    new, capped = [times[0]], set()
    for i in range(1, len(times)):
        gap = working_hours_between(times[i - 1], times[i])
        limit = rules.get(steps[i])
        if limit is not None and gap > limit:
            gap = limit
            capped.add(i)
        if gap > 0:
            t = add_working_hours(new[i - 1], gap)
        else:
            t = new[i - 1] + (times[i] - times[i - 1])   # happened out of hours; keep the offset
        new.append(min(t, times[i]))
    return new, capped


def replay(log: EventLog, digest: ChatDigest, pmap: ProcessMap,
           rules: dict[str, float]) -> ReplayResult:
    msgs = digest.messages
    names = {s.id: s.name for s in pmap.steps}
    chaser_times = {i: m.timestamp for i, m in enumerate(msgs) if FOLLOW_UP.search(m.text)}
    start, end = digest.date_range
    weeks = max((end - start).days / 7, 1.0)

    results, moments, avoided = [], [], set()
    any_linked = any(c.chaser_message_ids for c in log.cases)
    reach: dict[str, list[tuple[float, float]]] = {}
    for case in log.cases:
        steps = [e.step_id for e in case.events]
        times = [msgs[e.message_id].timestamp for e in case.events]
        new, capped = _simulate(times, steps, rules)
        # Prefer the chasers the event-log agent linked to this case; if it linked
        # none anywhere, fall back to chaser-like messages during the wait.
        candidates = ({i: msgs[i].timestamp for i in case.chaser_message_ids}
                      if any_linked else chaser_times)

        for i in range(1, len(times)):
            if i in capped:
                # Chasers sent during a wait the automation removed would not have
                # been needed. Knock-on shifts from earlier steps don't count.
                for cid, ct in candidates.items():
                    if times[i - 1] < ct < times[i] and ct > new[i]:
                        avoided.add(cid)
            if new[i] < times[i]:
                saved = working_hours_between(new[i], times[i])
                if saved >= 1:
                    moments.append(Moment(case_label=case.label, step_name=names.get(steps[i], steps[i]),
                                          before=times[i], after=new[i], hours_sooner=round(saved, 1)))

        for i, st in enumerate(steps):
            if i and st not in [x for x in steps[:i]]:      # first time the case reaches this step
                reach.setdefault(st, []).append((working_hours_between(times[0], times[i]),
                                                 working_hours_between(new[0], new[i])))

        results.append(CaseResult(
            case_id=case.case_id, label=case.label, start=times[0],
            end_before=times[-1], end_after=new[-1],
            working_hours_saved=round(working_hours_between(new[-1], times[-1]), 1)))

    n = len(results) or 1
    cyc_b = [working_hours_between(r.start, r.end_before) for r in results]
    cyc_a = [working_hours_between(r.start, r.end_after) for r in results]
    days_b = [(r.end_before - r.start).total_seconds() / 86400 for r in results]
    days_a = [(r.end_after - r.start).total_seconds() / 86400 for r in results]
    removed = sum(cyc_b) - sum(cyc_a)

    # Most striking moments, at most one per case
    best, seen = [], set()
    for m in sorted(moments, key=lambda m: m.hours_sooner, reverse=True):
        if m.case_label not in seen:
            best.append(m)
            seen.add(m.case_label)
        if len(best) == 5:
            break

    order = [x.id for x in pmap.steps]
    milestones = [
        Milestone(step_id=st, step_name=names.get(st, st), cases=len(v),
                  avg_hours_before=round(sum(b for b, _ in v) / len(v), 1),
                  avg_hours_after=round(sum(a for _, a in v) / len(v), 1))
        for st, v in sorted(reach.items(), key=lambda kv: order.index(kv[0]) if kv[0] in order else 99)
        if len(v) >= 3
    ]

    return ReplayResult(
        rules=rules, cases=len(results),
        avg_cycle_before_h=round(sum(cyc_b) / n, 1), avg_cycle_after_h=round(sum(cyc_a) / n, 1),
        avg_days_before=round(sum(days_b) / n, 1), avg_days_after=round(sum(days_a) / n, 1),
        waiting_removed_h_total=round(removed, 1),
        waiting_removed_per_week=round(removed / weeks, 1),
        chasers_avoided=len(avoided),
        chasers_avoided_per_week=round(len(avoided) / weeks, 1),
        moments=best, milestones=milestones, case_results=results,
    )

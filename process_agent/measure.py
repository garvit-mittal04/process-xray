"""Measure step frequency and wait times from real chat timestamps.

Waits are counted in working hours (Mon-Sat, 09:00-19:00 by default), so a
message sent at 7 pm and answered at 10 am next day counts as 1 hour, not 15."""
import statistics
from datetime import datetime, timedelta

from .mapper import ProcessMap
from .schema import ChatDigest

WORK_START, WORK_END = 9, 19      # 9 am to 7 pm
WORK_DAYS = {0, 1, 2, 3, 4, 5}    # Monday to Saturday


def working_hours_between(a: datetime, b: datetime) -> float:
    if b <= a:
        return 0.0
    total = 0.0
    day = a.replace(hour=0, minute=0, second=0, microsecond=0)
    while day <= b:
        if day.weekday() in WORK_DAYS:
            open_t = day.replace(hour=WORK_START)
            close_t = day.replace(hour=WORK_END)
            start, end = max(a, open_t), min(b, close_t)
            if end > start:
                total += (end - start).total_seconds() / 3600
        day += timedelta(days=1)
    return total


def _wait_before(messages, i: int) -> float:
    """Working hours between message i and the last message from someone else."""
    j = i - 1
    while j >= 0 and messages[j].sender == messages[i].sender:
        j -= 1
    if j < 0:
        return 0.0
    return working_hours_between(messages[j].timestamp, messages[i].timestamp)


SAME_OCCURRENCE_MINUTES = 60


def _occurrences(messages, ids: list[int]) -> list[int]:
    """Group cited messages into occurrences. Messages from the same person
    within an hour (e.g. "PO attached" + the PDF itself) count as one
    occurrence. Returns the first message id of each occurrence."""
    firsts: list[int] = []
    for i in ids:
        if firsts:
            prev = messages[firsts[-1]]
            gap = (messages[i].timestamp - prev.timestamp).total_seconds() / 60
            if messages[i].sender == prev.sender and gap <= SAME_OCCURRENCE_MINUTES:
                continue
        firsts.append(i)
    return firsts


def measure_steps(pmap: ProcessMap, digest: ChatDigest) -> ProcessMap:
    msgs = digest.messages
    start, end = digest.date_range
    weeks = max((end - start).days / 7, 1.0)

    for step in pmap.steps:
        ids = sorted({i for i in step.message_ids if 0 <= i < len(msgs)})
        step.message_ids = ids
        assumed = ["minutes_per_run"]
        if ids:
            occ = _occurrences(msgs, ids)
            step.frequency_per_week = round(len(occ) / weeks, 2)
            if step.starts_on_external_event:
                step.wait_before_hours = 0.0
            else:
                step.wait_before_hours = round(
                    statistics.median(_wait_before(msgs, i) for i in occ), 1)
        else:
            step.frequency_per_week = round(1 / weeks, 2)
            step.wait_before_hours = 0.0
            assumed += ["frequency_per_week", "wait_before_hours"]
        step.assumed_fields = assumed

    for p in pmap.pain_points:
        p.message_ids = sorted({i for i in p.message_ids if 0 <= i < len(msgs)})
    return pmap

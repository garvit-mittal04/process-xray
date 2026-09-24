"""Parse WhatsApp chat exports (Android and iPhone), redact personal data,
and extract process signals such as wait times and follow-up chasers."""
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from .schema import ChatDigest, Message, ResponseStat

# Android: "12/03/24, 10:15 am - Name: text"
ANDROID = re.compile(
    r"^(\d{1,2}/\d{1,2}/\d{2,4}),\s(\d{1,2}:\d{2}(?:\s?[apAP]\.?[mM]\.?)?)\s-\s(.*?):\s(.*)$"
)
# iPhone: "[12/03/24, 10:15:32 AM] Name: text"
IOS = re.compile(
    r"^\[(\d{1,2}/\d{1,2}/\d{2,4}),\s(\d{1,2}:\d{2}(?::\d{2})?(?:\s?[apAP][mM])?)\]\s(.*?):\s(.*)$"
)

PHONE = re.compile(r"\+?\d[\d\s-]{8,}\d")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
ATTACHMENT = re.compile(
    r"<media omitted>|<attached:|\(file attached\)|document omitted|image omitted|\.pdf\b|\.xlsx?\b",
    re.I,
)
# English + Hinglish phrases people use when chasing a pending task
FOLLOW_UP = re.compile(
    r"any update|update\?|reminder|still waiting|please check|pls check|kab tak|"
    r"kya hua|abhi tak|jaldi|urgent|asap|gentle reminder|following up",
    re.I,
)

DATE_FORMATS = ["%d/%m/%y", "%d/%m/%Y", "%m/%d/%y", "%m/%d/%Y"]

# Business names mentioned inside messages ("Gupta Stores", "Mehta ji") belong to
# customers and suppliers, not participants. They are masked as "Customer A",
# "Customer B"... before anything is sent to an AI model.
BUSINESS_SUFFIX = re.compile(
    r"\b([A-Z][a-z]{2,})(?:\s[A-Z][a-z]+)?\s(?:ji|Ji|Stores?|Traders?|Mart|Retail|Kirana|"
    r"Distributors?|Provisions?|Supermart|Enterprises|Industries|Agencies|Brothers|Sons|"
    r"Pvt|Ltd|Co|Company|Foods|Textiles|Electricals|Hardware|Medicals?)\b"
)
NOT_NAMES = {"Sir", "Madam", "Bhai", "Haan", "Nahi", "Please", "Invoice", "Order", "Stock",
             "Payment", "Dispatch", "Approved", "Sorry", "Theek", "Customer", "Person"}


def _parse_datetime(date_str: str, time_str: str, day_first: bool) -> datetime:
    time_str = time_str.replace("\u202f", " ").replace(".", "").upper().strip()
    formats = DATE_FORMATS if day_first else DATE_FORMATS[2:] + DATE_FORMATS[:2]
    time_formats = ["%I:%M %p", "%I:%M:%S %p", "%I:%M%p", "%H:%M", "%H:%M:%S"]
    for df in formats:
        for tf in time_formats:
            try:
                return datetime.strptime(f"{date_str} {time_str}", f"{df} {tf}")
            except ValueError:
                continue
    raise ValueError(f"Unrecognised timestamp: {date_str} {time_str}")


def _redact(text: str, name_map: dict[str, str]) -> str:
    text = EMAIL.sub("[email]", text)
    text = PHONE.sub("[phone]", text)
    # Replace full names first, then first names, so "Ravi Kumar" -> "Person 2"
    for real in sorted(name_map, key=len, reverse=True):
        text = re.sub(rf"\b{re.escape(real)}\b", name_map[real], text, flags=re.I)
    return text


def _mask_businesses(texts: list[str]) -> list[str]:
    """Replace customer/supplier names with Customer A, B, ... consistently."""
    names: dict[str, str] = {}
    for t in texts:
        for m in BUSINESS_SUFFIX.finditer(t):
            first = m.group(1)
            if first not in NOT_NAMES and first not in names:
                names[first] = f"Customer {chr(65 + len(names) % 26)}{'' if len(names) < 26 else len(names) // 26}"
    if not names:
        return texts
    # Match the name even inside file names like PO_Mehta_0303.pdf
    pattern = re.compile(r"(?<![A-Za-z])(" + "|".join(map(re.escape, names)) + r")(?![a-z])")
    return [pattern.sub(lambda m: names[m.group(1)], t) for t in texts]


def parse_whatsapp(path: str | Path, day_first: bool = True, mask_customers: bool = True) -> list[Message]:
    """Read an exported chat file and return redacted, pseudonymised messages."""
    return parse_whatsapp_text(Path(path).read_text(encoding="utf-8"), day_first, mask_customers)


def parse_whatsapp_text(content: str, day_first: bool = True, mask_customers: bool = True) -> list[Message]:
    """Parse the text of an exported chat (nothing is written to disk)."""
    raw: list[tuple[datetime, str, str]] = []
    for line in content.splitlines():
        line = line.lstrip("\u200e\ufeff").rstrip()
        match = ANDROID.match(line) or IOS.match(line)
        if match:
            date_s, time_s, sender, text = match.groups()
            raw.append((_parse_datetime(date_s, time_s, day_first), sender.strip(), text))
        elif raw and line:
            # Continuation of a multi-line message
            ts, sender, text = raw[-1]
            raw[-1] = (ts, sender, text + "\n" + line)
        # Lines matching neither are system notices ("X added Y") and are skipped

    # Build pseudonyms in order of first appearance
    name_map: dict[str, str] = {}
    for _, sender, _ in raw:
        if sender not in name_map:
            name_map[sender] = f"Person {len(name_map) + 1}"
    for real, alias in list(name_map.items()):
        first = real.split()[0]
        if len(first) > 2 and not first.startswith("+") and first not in name_map:
            name_map[first] = alias

    texts = [_redact(text, name_map) for _, _, text in raw]
    if mask_customers:
        texts = _mask_businesses(texts)
    return [
        Message(
            timestamp=ts,
            sender=name_map[sender],
            text=clean,
            has_attachment=bool(ATTACHMENT.search(text)),
        )
        for (ts, sender, text), clean in zip(raw, texts)
    ]


def build_digest(messages: list[Message], source_name: str) -> ChatDigest:
    """Turn messages into process signals the Mapper agent can reason over."""
    if not messages:
        raise ValueError("No messages found. Check the export format.")

    participants = Counter(m.sender for m in messages)

    # Response time: gap between a message and the next message from someone else
    waits: dict[str, list[float]] = defaultdict(list)
    for prev, nxt in zip(messages, messages[1:]):
        if nxt.sender != prev.sender:
            minutes = (nxt.timestamp - prev.timestamp).total_seconds() / 60
            waits[nxt.sender].append(minutes)

    response_stats = sorted(
        (
            ResponseStat(
                responder=who,
                replies=len(gaps),
                median_minutes=round(statistics.median(gaps), 1),
                slowest_minutes=round(max(gaps), 1),
            )
            for who, gaps in waits.items()
        ),
        key=lambda s: s.median_minutes,
        reverse=True,
    )

    follow_ups = [m for m in messages if FOLLOW_UP.search(m.text)]
    hours = Counter(m.timestamp.hour for m in messages)

    return ChatDigest(
        source_name=source_name,
        date_range=(messages[0].timestamp, messages[-1].timestamp),
        total_messages=len(messages),
        participants=dict(participants.most_common()),
        response_stats=response_stats,
        follow_up_count=len(follow_ups),
        follow_up_examples=[f"{m.sender}: {m.text[:80]}" for m in follow_ups[:5]],
        attachment_count=sum(m.has_attachment for m in messages),
        busiest_hours=[h for h, _ in hours.most_common(3)],
        messages=messages,
    )

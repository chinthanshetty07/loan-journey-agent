"""Formatting for text-to-speech: Indian-style amounts and times a TTS engine reads naturally."""
from __future__ import annotations

import re
from datetime import datetime


def format_inr(amount: int) -> str:
    """1500000 -> ₹15,00,000 (Indian digit grouping)."""
    s = str(amount)
    if len(s) <= 3:
        return f"₹{s}"
    head, tail = s[:-3], s[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return "₹" + ",".join(groups) + "," + tail


def speak_inr(amount: int) -> str:
    """1550000 -> '15 lakh 50 thousand rupees'."""
    parts = []
    crore, rest = divmod(amount, 10_000_000)
    lakh, rest = divmod(rest, 100_000)
    thousand, rest = divmod(rest, 1_000)
    if crore:
        parts.append(f"{crore} crore")
    if lakh:
        parts.append(f"{lakh} lakh")
    if thousand:
        parts.append(f"{thousand} thousand")
    if rest or not parts:
        parts.append(str(rest))
    return " ".join(parts) + " rupees"


def format_time(when: datetime) -> str:
    """18:00 -> '6 pm', 09:30 -> '9:30 am'."""
    hour = when.hour % 12 or 12
    minutes = f":{when.minute:02d}" if when.minute else ""
    return f"{hour}{minutes} {'am' if when.hour < 12 else 'pm'}"


_RUPEES = re.compile(r"₹([\d,]+)")


def to_speech(text: str) -> str:
    text = _RUPEES.sub(lambda m: speak_inr(int(m.group(1).replace(",", ""))), text)
    text = text.replace("e-sign", "e sign").replace("KYC", "K Y C").replace("EMI", "E M I")
    return re.sub(r"[()\[\]*_#]", "", text)

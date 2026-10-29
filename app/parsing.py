"""Deterministic slot extraction. Amounts, tenures and times are always parsed by code,
never taken from a model, because the agent acts on them."""
from __future__ import annotations

import re
from datetime import datetime, time, timedelta

_TENURE = re.compile(r"\b(\d{1,3})\s*(months?|mo|mnths?|years?|yrs?)\b", re.IGNORECASE)
_AMOUNT = re.compile(
    r"(?:₹|rs\.?|inr)?\s*(\d[\d,]*(?:\.\d+)?)\s*(crores?|cr|lakhs?|lacs?|lac|l|k|thousand)?\b",
    re.IGNORECASE,
)
_UNITS = {"cr": 10_000_000, "crore": 10_000_000, "crores": 10_000_000,
          "lakh": 100_000, "lakhs": 100_000, "lac": 100_000, "lacs": 100_000, "l": 100_000,
          "k": 1_000, "thousand": 1_000}
_TIME = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", re.IGNORECASE)


def parse_tenure_months(text: str) -> int | None:
    m = _TENURE.search(text)
    if not m:
        return None
    n = int(m.group(1))
    months = n * 12 if m.group(2).lower().startswith(("y")) else n
    return months if 1 <= months <= 120 else None


def parse_amount_inr(text: str) -> int | None:
    text = _TENURE.sub(" ", text)  # "2 lakh for 24 months": the 24 is not an amount
    text = _TIME.sub(" ", text)
    for m in _AMOUNT.finditer(text):
        number = float(m.group(1).replace(",", ""))
        unit = (m.group(2) or "").lower()
        has_currency = bool(re.match(r"\s*(₹|rs|inr)", m.group(0), re.IGNORECASE))
        if unit:
            value = number * _UNITS[unit]
        elif has_currency or number >= 1000:
            value = number
        else:
            continue
        if 1_000 <= value <= 50_000_000:
            return int(value)
    return None


def parse_callback_time(text: str, now: datetime) -> datetime | None:
    m = _TIME.search(text)
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    if not 1 <= hour <= 12 or minute > 59:
        return None
    hour = hour % 12 + (12 if m.group(3).lower() == "pm" else 0)
    lowered = text.lower()
    tomorrow = "tomorrow" in lowered or re.search(r"\bkal\b", lowered) is not None
    day = now.date() + timedelta(days=1 if tomorrow else 0)
    when = datetime.combine(day, time(hour, minute), tzinfo=now.tzinfo)
    if when <= now:  # "at 10 am" said at 3 pm means tomorrow
        when += timedelta(days=1)
    return when


def within_window(when: datetime, start_hour: int, end_hour: int) -> bool:
    return start_hour <= when.hour < end_hour


def next_allowed_slot(when: datetime, start_hour: int, end_hour: int) -> datetime:
    if when.hour < start_hour:
        return when.replace(hour=start_hour, minute=0)
    return (when + timedelta(days=1)).replace(hour=start_hour, minute=0)


def emi_inr(principal: int, annual_rate_pct: float, months: int) -> int:
    r = annual_rate_pct / 12 / 100
    if r == 0:
        return round(principal / months)
    factor = (1 + r) ** months
    return round(principal * r * factor / (factor - 1))

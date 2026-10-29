from datetime import datetime

import pytest

from app.config import IST
from app.guardrails import redact, violates_compliance
from app.parsing import (emi_inr, next_allowed_slot, parse_amount_inr, parse_callback_time,
                         parse_tenure_months, within_window)
from app.voice import format_inr, format_time, speak_inr, to_speech

NOW = datetime(2026, 10, 29, 15, 0, tzinfo=IST)


@pytest.mark.parametrize("text,amount", [
    ("emi for 2 lakh", 200_000), ("₹1,50,000 please", 150_000), ("50k loan", 50_000),
    ("rs 300000", 300_000), ("1.5 lakh for 24 months", 150_000), ("2 cr", 20_000_000),
    ("what is my emi", None), ("for 24 months", None),
])
def test_amounts(text, amount):
    assert parse_amount_inr(text) == amount


@pytest.mark.parametrize("text,months", [("24 months", 24), ("2 years", 24), ("for 3 yrs", 36), ("emi", None), ("500 months", None)])
def test_tenure(text, months):
    assert parse_tenure_months(text) == months


def test_callback_time_today_tomorrow_and_past():
    assert parse_callback_time("call at 6 pm", NOW) == NOW.replace(hour=18)
    assert parse_callback_time("tomorrow 10:30am", NOW) == datetime(2026, 10, 30, 10, 30, tzinfo=IST)
    assert parse_callback_time("at 10 am", NOW) == datetime(2026, 10, 30, 10, 0, tzinfo=IST)  # already past today
    assert parse_callback_time("kal 11 am", NOW).day == 30
    assert parse_callback_time("call me later", NOW) is None
    assert parse_callback_time("13 pm", NOW) is None


def test_contact_window():
    assert within_window(NOW.replace(hour=9), 9, 19) and not within_window(NOW.replace(hour=19), 9, 19)
    assert next_allowed_slot(NOW.replace(hour=7), 9, 19) == NOW.replace(hour=9)
    assert next_allowed_slot(NOW.replace(hour=21), 9, 19) == datetime(2026, 10, 30, 9, 0, tzinfo=IST)


def test_emi_formula():
    assert emi_inr(100_000, 12.0, 12) == 8885  # standard reducing-balance result
    assert emi_inr(120_000, 0, 12) == 10_000


def test_indian_formatting_and_speech():
    assert format_inr(1_550_000) == "₹15,50,000"
    assert format_inr(999) == "₹999"
    assert speak_inr(1_550_000) == "15 lakh 50 thousand rupees"
    assert speak_inr(20_000_000) == "2 crore rupees"
    assert format_time(NOW.replace(hour=18)) == "6 pm" and format_time(NOW.replace(hour=9, minute=30)) == "9:30 am"
    assert to_speech("EMI of ₹12,383 after KYC") == "E M I of 12 thousand 383 rupees after K Y C"


def test_redaction_covers_indian_identifiers_but_not_amounts():
    text = "PAN ABCDE1234F, aadhaar 1234 5678 9012, call +91 98765 43210, mail a.b@x.in, acct 123456789012345, need 200000"
    out = redact(text)
    for secret in ["ABCDE1234F", "1234 5678 9012", "98765 43210", "a.b@x.in", "123456789012345"]:
        assert secret not in out
    assert "[PAN]" in out and "[AADHAAR]" in out and "[PHONE]" in out and "[EMAIL]" in out and "[ACCOUNT]" in out
    assert "200000" in out


def test_compliance_gate():
    assert violates_compliance("You have guaranteed approval!")
    assert violates_compliance("No credit check needed")
    assert not violates_compliance("Your loan of ₹2,00,000 is approved.")

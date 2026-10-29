"""Redaction before anything is stored or sent to a model, and a compliance gate on outgoing text."""
from __future__ import annotations

import re

# Order matters: the longer, more specific patterns run first so a 12-digit Aadhaar
# is not half-matched as a phone number.
_PATTERNS = [
    ("AADHAAR", re.compile(r"\b\d{4}[ -]?\d{4}[ -]?\d{4}\b")),
    ("PAN", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b", re.IGNORECASE)),
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")),
    ("PHONE", re.compile(r"(?:\+91[ -]?)?\b[6-9]\d{4}[ -]?\d{5}\b")),
    ("ACCOUNT", re.compile(r"\b\d{9,18}\b")),
]


def redact(text: str) -> str:
    for label, pattern in _PATTERNS:
        text = pattern.sub(f"[{label}]", text)
    return text


# Phrases a lender's agent must never say. Replies are templated today, but this gate
# sits in front of every outgoing message so a future LLM-written reply cannot skip it.
_BANNED = re.compile(
    r"guarantee[d]?\s+(?:loan\s+)?approval|100\s*%\s*approval|no\s+credit\s+check|"
    r"approval\s+is\s+guaranteed|lowest\s+rate\s+guaranteed",
    re.IGNORECASE,
)


def violates_compliance(text: str) -> bool:
    return bool(_BANNED.search(text))

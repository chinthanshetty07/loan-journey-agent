"""Intent understanding. The model (when enabled) only classifies; code decides and acts."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

INTENTS = [
    "greeting", "application_status", "kyc_help", "documents_help", "emi_quote",
    "schedule_callback", "affirm", "deny", "talk_to_human", "complaint", "opt_out", "unknown",
]
SENTIMENTS = ["neutral", "positive", "frustrated", "angry"]


@dataclass
class Intent:
    name: str
    confidence: float
    sentiment: str = "neutral"
    provider: str = "rules"
    usage: dict = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0})


# Checked in order; the first match wins. A few common Hinglish phrasings are included,
# which is not the same as real multilingual support.
_RULES: list[tuple[str, re.Pattern]] = [
    ("opt_out", re.compile(r"stop (calling|messaging|contacting)|do ?n[o']?t (call|contact|message)|unsubscribe|call mat karo|mat call", re.I)),
    ("complaint", re.compile(r"complain|complaint|grievance|escalat|ombudsman", re.I)),
    ("talk_to_human", re.compile(r"\b(human|real person|executive|customer care|representative|agent)\b|insaan|kisi se baat", re.I)),
    ("schedule_callback", re.compile(r"call (me )?(back|later)|callback|call me (at|tomorrow|today|in)|\bcall\b.*\b(am|pm)\b|baad mein call", re.I)),
    ("kyc_help", re.compile(r"\bkyc\b|verification link|video verification|kyc link", re.I)),
    ("documents_help", re.compile(r"document|bank statement|salary slip|payslip|upload|kaunse papers", re.I)),
    ("emi_quote", re.compile(r"\bemi\b|monthly (payment|instal?ment)|per month|kitna (dena|bharna)|installment", re.I)),
    ("application_status", re.compile(r"status|where is my (loan|application)|update on|approved\??$|kya hua|kab tak|any news|my application", re.I)),
    ("affirm", re.compile(r"^(yes|yeah|yep|ok(ay)?|sure|haan|ha|theek hai|done|please do|go ahead)\b", re.I)),
    ("deny", re.compile(r"^(no|nope|nahi|not now|later)\b", re.I)),
    ("greeting", re.compile(r"^(hi|hello|hey|namaste|good (morning|afternoon|evening))\b", re.I)),
]
_ANGRY = re.compile(r"worst|fraud|cheat|useless|pathetic|harass|scam|disgusting|ridiculous|bakwaas", re.I)
_FRUSTRATED = re.compile(r"still|again|already told|how many times|why is it taking|so long", re.I)


class RuleNLU:
    name = "rules"

    def parse(self, text: str, context: str = "") -> Intent:
        sentiment = "angry" if _ANGRY.search(text) else "frustrated" if _FRUSTRATED.search(text) else "neutral"
        for intent, pattern in _RULES:
            if pattern.search(text.strip()):
                return Intent(intent, 0.9, sentiment)
        return Intent("unknown", 0.0, sentiment)


SYSTEM_PROMPT = (
    "You classify customer messages for an Indian lender's loan-application assistant.\n"
    "Messages may be English, Hindi or Hinglish. You are given the assistant's previous message for context.\n"
    "Pick exactly one intent. Use 'unknown' if the message fits none, and lower confidence when unsure.\n"
    "Intent guide: application_status = asking where the loan stands; kyc_help = KYC or verification link; "
    "documents_help = which papers to upload; emi_quote = monthly payment amount; schedule_callback = wants a call "
    "at a later time; affirm/deny = yes/no to the assistant's question; talk_to_human = wants a person; "
    "complaint = formal complaint or escalation; opt_out = wants no more calls or messages."
)

NLU_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": INTENTS},
        "confidence": {"type": "number"},
        "sentiment": {"type": "string", "enum": SENTIMENTS},
    },
    "required": ["intent", "confidence", "sentiment"],
    "additionalProperties": False,
}


class ClaudeNLU:
    """Classifies with Claude structured outputs. Enabled with NLU_PROVIDER=claude.

    Input is already redacted. On a refusal, unparseable output or an API error it falls
    back to the rule-based NLU, so a model outage degrades quality instead of availability.
    """

    name = "claude"
    model = "claude-opus-5-5"
    usd_per_mtok_in = 4.00
    usd_per_mtok_out = 20.00

    def __init__(self, client=None, fallback: RuleNLU | None = None, effort: str = "low") -> None:
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self._client = client
        self._fallback = fallback or RuleNLU()
        self._effort = effort

    def parse(self, text: str, context: str = "") -> Intent:
        try:
            response = self._client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                system=SYSTEM_PROMPT,
                output_config={"effort": self._effort, "format": {"type": "json_schema", "schema": NLU_SCHEMA}},
                messages=[{
                    "role": "user",
                    "content": f"<assistant_previous>\n{context}\n</assistant_previous>\n\n<customer>\n{text}\n</customer>",
                }],
            )
        except Exception as exc:  # network, rate limit or server error: degrade, don't fail the turn
            return self._degraded(text, context, f"error:{type(exc).__name__}")

        usage = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "cost_usd": round(
                response.usage.input_tokens * self.usd_per_mtok_in / 1e6
                + response.usage.output_tokens * self.usd_per_mtok_out / 1e6,
                6,
            ),
        }
        if response.stop_reason == "refusal":
            return self._degraded(text, context, "refusal", usage)
        raw = next((b.text for b in response.content if b.type == "text"), "")
        try:
            data = json.loads(raw)
            confidence = min(max(float(data["confidence"]), 0.0), 1.0)
            return Intent(data["intent"], confidence, data["sentiment"], self.name, usage)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return self._degraded(text, context, "unparseable", usage)

    def _degraded(self, text: str, context: str, why: str, usage: dict | None = None) -> Intent:
        intent = self._fallback.parse(text, context)
        intent.provider = f"rules (claude {why})"
        if usage:
            intent.usage = usage
        return intent

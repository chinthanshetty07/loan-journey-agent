"""Turn orchestration: redact -> understand -> apply policy -> run a deterministic tool -> gate -> format.

The model never writes customer-facing text and never produces numbers the agent acts on.
Replies come from templates filled with values from the application record and code-parsed slots.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Callable

from .config import Settings, now_ist
from .guardrails import redact, violates_compliance
from .metrics import Metrics
from .nlu import Intent
from .parsing import emi_inr, next_allowed_slot, parse_amount_inr, parse_callback_time, parse_tenure_months, within_window
from .store import Application, Session, Store
from .voice import format_inr, format_time, to_speech

# Intents that reveal application details; on an unverified voice call these wait for verification.
DISCLOSING = {"application_status", "emi_quote", "documents_help"}

MENU = "your application status, KYC, documents, EMI or booking a callback"


class Agent:
    def __init__(self, store: Store, nlu, settings: Settings, metrics: Metrics | None = None,
                 clock: Callable[[], datetime] = now_ist) -> None:
        self.store = store
        self.nlu = nlu
        self.settings = settings
        self.metrics = metrics or Metrics()
        self.clock = clock

    def greeting(self, app: Application) -> str:
        return f"Hi {app.first_name}, I can help with {MENU}. What would you like to do?"

    def handle(self, session: Session, text: str, idempotency_key: str | None = None) -> dict:
        if idempotency_key:
            previous = self.store.find_turn(session.id, idempotency_key)
            if previous is not None:  # a retried request (flaky network, voice gateway retry): replay, don't redo
                return {**previous, "replayed": True}

        started = time.perf_counter()
        now = self.clock()
        app = self.store.application(session.application_id)
        redacted = redact(text)
        turn = _Turn(session, app, now)

        if session.handed_off:
            intent = Intent("handed_off", 1.0, provider="none")
            turn.reply = "An executive has this conversation and will reply here shortly."
        else:
            intent = self.nlu.parse(redacted, context=session.pending or "")
            self._decide(turn, intent, redacted)

        if violates_compliance(turn.reply):  # last gate before anything reaches the customer
            turn.handoff("compliance_block", "Let me connect you to an executive who can help with that.")

        session.handed_off = session.handed_off or turn.handoff_reason is not None
        self.store.save_session(session)
        for action in turn.actions:
            self.store.record_action(session.id, action["type"], {k: v for k, v in action.items() if k != "type"}, now.isoformat())

        latency_ms = (time.perf_counter() - started) * 1000
        result = {
            "session_id": session.id,
            "intent": turn.intent_name or intent.name,
            "confidence": intent.confidence,
            "sentiment": intent.sentiment,
            "reply": turn.reply,
            "tts_text": to_speech(turn.reply) if session.channel == "voice" else None,
            "actions": turn.actions,
            "handoff_reason": turn.handoff_reason,
            "stage": app.stage,
            "nlu_provider": intent.provider,
            "usage": intent.usage,
            "replayed": False,
        }
        self.store.record_turn(session.id, idempotency_key, redacted, result["intent"], result, now.isoformat())
        self.metrics.record(result["intent"], latency_ms, turn.handoff_reason, intent.provider, intent.usage)
        return result

    # policy
    def _decide(self, t: "_Turn", intent: Intent, text: str) -> None:
        s = t.session
        name = intent.name
        when = parse_callback_time(text, t.now)

        # Slot filling: a short answer ("9 pm", "yes") to the agent's last question is understood
        # from that question, even when the message alone would not be.
        slot_filled = True
        if s.pending == "callback_time" and when and name in ("unknown", "affirm", "schedule_callback"):
            name = "schedule_callback"
        elif s.pending == "confirm_slot" and name == "affirm":
            name = "confirm_slot"
        elif s.pending == "offer_kyc" and name == "affirm":
            name = "kyc_help"
        else:
            slot_filled = False
        t.intent_name = name

        if intent.sentiment == "angry" or name == "talk_to_human":
            t.handoff("customer_request" if name == "talk_to_human" else "angry_customer",
                      "I'm connecting you to an executive now. They'll see this conversation, so you won't need to repeat yourself.")
            return
        if name == "complaint":
            t.handoff("complaint", "I've noted your complaint and I'm connecting you to an executive who can take it forward.")
            return
        if name == "opt_out":
            s.consent = False
            s.pending = None
            t.act("consent_revoked")
            t.reply = "Understood. We won't call or message you about this application. You can still reach us in the app anytime."
            return

        if not slot_filled and (name == "unknown" or intent.confidence < self.settings.min_confidence):
            s.unknown_streak += 1
            if s.unknown_streak >= 2:
                t.handoff("not_understood", "Let me get an executive to help you with this.")
            else:
                t.reply = f"Sorry, I didn't catch that. I can help with {MENU}."
            return
        s.unknown_streak = 0

        if name in DISCLOSING and s.channel == "voice" and not s.verified:
            t.act("verification_required")
            t.reply = ("For your security, please verify with the OTP sent to your registered mobile number "
                       "before I share application details.")
            return

        handler = {
            "greeting": self._greeting,
            "application_status": self._status,
            "kyc_help": self._kyc,
            "documents_help": self._documents,
            "emi_quote": self._emi,
            "schedule_callback": self._callback,
            "confirm_slot": self._confirm_slot,
            "affirm": self._ack,
            "deny": self._deny,
        }[name]
        s.pending = None  # a handler sets it again if it asks a follow-up question
        handler(t, text, when)

    def _greeting(self, t, text, when):
        t.reply = self.greeting(t.app)

    def _status(self, t: "_Turn", text, when):
        a = t.app
        amount = format_inr(a.amount_inr)
        if a.stage == "started":
            t.reply = "Your application is saved but not submitted yet. You can finish it in the app in a few minutes."
        elif a.stage == "kyc_pending":
            t.session.pending = "offer_kyc"
            t.reply = "Your application is waiting for video KYC. Shall I send the KYC link to your registered mobile number?"
        elif a.stage == "documents_pending":
            t.reply = f"We need your {_join(a.pending_documents)} to continue. You can upload them in the app under Documents."
        elif a.stage == "under_review":
            t.reply = "Your application is under review. You'll get an update in the app as soon as there's a decision."
        elif a.stage == "approved":
            t.reply = f"Good news: your loan of {amount} is approved. The next step is e-sign in the app."
        elif a.stage == "esign_pending":
            t.reply = f"Your loan of {amount} is approved and waiting for your e-sign in the app."
        elif a.stage == "disbursed":
            t.reply = f"Your loan of {amount} has been disbursed to your bank account."
        else:  # rejected: explaining a decline is a person's job
            t.handoff("decline_explanation",
                      "I'm sorry, your application wasn't approved this time. I'm connecting you to an executive who can explain the decision.")

    def _kyc(self, t: "_Turn", text, when):
        a = t.app
        if a.stage == "started":
            t.reply = "KYC comes after you submit your application in the app."
            return
        if a.stage != "kyc_pending":
            t.reply = "Your KYC is already complete, so there's nothing more to do there."
            return
        day_start = t.now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        sent_today = self.store.count_actions_since(a.id, "kyc_link_sent", day_start)
        if sent_today >= self.settings.max_kyc_resends_per_day:
            t.handoff("kyc_resend_limit", "The KYC link has already been sent several times today. I'm connecting you to an executive to sort it out.")
            return
        t.act("kyc_link_sent", channel="sms_to_registered_mobile")
        t.reply = "Done. I've sent the video KYC link to your registered mobile number."

    def _documents(self, t: "_Turn", text, when):
        a = t.app
        if a.stage == "documents_pending" and a.pending_documents:
            t.reply = f"We still need your {_join(a.pending_documents)}. You can upload them in the app under Documents."
        else:
            t.reply = "There are no documents pending on your application."

    def _emi(self, t: "_Turn", text, when):
        a = t.app
        amount = parse_amount_inr(text) or a.amount_inr
        months = parse_tenure_months(text) or a.tenure_months
        emi = emi_inr(amount, a.rate_annual_pct, months)
        t.act("emi_quote", amount_inr=amount, tenure_months=months, rate_annual_pct=a.rate_annual_pct, emi_inr=emi)
        t.reply = (f"For {format_inr(amount)} over {months} months at {a.rate_annual_pct:g}% a year, the EMI would be about "
                   f"{format_inr(emi)} a month. Your loan agreement will show the exact amount.")

    def _callback(self, t: "_Turn", text, when):
        cfg = self.settings
        window = f"{cfg.contact_start_hour % 12 or 12} am and {cfg.contact_end_hour % 12 or 12} pm"
        if not t.session.consent:  # an explicit request for a call is fresh consent; keep an audit record
            t.session.consent = True
            t.act("consent_granted", via="callback_request")
        if when is None:
            t.session.pending = "callback_time"
            t.reply = f"Sure. What time works for you? We can call between {window}."
            return
        if within_window(when, cfg.contact_start_hour, cfg.contact_end_hour):
            self._book(t, when)
            return
        slot = next_allowed_slot(when, cfg.contact_start_hour, cfg.contact_end_hour)
        t.session.pending = "confirm_slot"
        t.session.proposed_slot = slot.isoformat()
        t.reply = f"We only call between {window}. Shall I book {_day(slot, t.now)} at {format_time(slot)} instead?"

    def _confirm_slot(self, t: "_Turn", text, when):
        self._book(t, datetime.fromisoformat(t.session.proposed_slot))

    def _book(self, t: "_Turn", when: datetime):
        t.session.proposed_slot = None
        t.act("callback_scheduled", at=when.isoformat())
        t.reply = f"Booked. We'll call you {_day(when, t.now)} at {format_time(when)}."

    def _ack(self, t, text, when):
        t.reply = "Okay. Is there anything else I can help with on your application?"

    def _deny(self, t, text, when):
        t.reply = f"No problem. I can also help with {MENU}."


class _Turn:
    def __init__(self, session: Session, app: Application, now: datetime) -> None:
        self.session = session
        self.app = app
        self.now = now
        self.reply = ""
        self.actions: list[dict] = []
        self.handoff_reason: str | None = None
        self.intent_name: str | None = None

    def act(self, type_: str, **detail) -> None:
        self.actions.append({"type": type_, **detail})

    def handoff(self, reason: str, reply: str) -> None:
        self.handoff_reason = reason
        self.session.pending = None
        self.act("handoff", reason=reason)
        self.reply = reply


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _day(when: datetime, now: datetime) -> str:
    if when.date() == now.date():
        return "today"
    if when.date() == (now + timedelta(days=1)).date():
        return "tomorrow"
    return when.strftime("on %d %b")

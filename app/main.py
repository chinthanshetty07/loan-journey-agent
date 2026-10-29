"""HTTP API for the loan-journey agent."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

from .agent import Agent
from .config import Settings, now_ist
from .metrics import Metrics
from .nlu import ClaudeNLU, RuleNLU
from .store import STAGES, Store

DATA = Path(__file__).resolve().parent.parent / "data" / "applications.json"
log = logging.getLogger("loan_agent")


class SessionIn(BaseModel):
    customer_id: str = Field(min_length=1, max_length=64)
    channel: Literal["in_app", "voice", "whatsapp"]
    verified: bool = False  # set by the lender's app or IVR after login/OTP; the agent never verifies itself


class TurnIn(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    idempotency_key: str | None = Field(default=None, max_length=64)


class EventIn(BaseModel):
    event_id: str = Field(min_length=1, max_length=64)
    application_id: str
    stage: str


def create_app(settings: Settings | None = None, nlu=None, clock=now_ist) -> FastAPI:
    settings = settings or Settings.from_env()
    store = Store(settings.db_path)
    store.seed(DATA)
    if nlu is None:
        nlu = ClaudeNLU() if settings.nlu_provider == "claude" else RuleNLU()
    metrics = Metrics()
    agent = Agent(store, nlu, settings, metrics, clock)

    app = FastAPI(title="Loan Journey Agent", version="0.1.0")
    app.state.store = store
    app.state.agent = agent

    @app.get("/health")
    def health():
        return {"status": "ok", "nlu": nlu.name}

    @app.post("/v1/sessions", status_code=201)
    def create_session(body: SessionIn):
        application = store.application_for_customer(body.customer_id)
        if application is None:
            raise HTTPException(404, "No application found for this customer")
        session = store.create_session(body.customer_id, application.id, body.channel, body.verified, clock().isoformat())
        greeting = agent.greeting(application)
        return {"session_id": session.id, "application_id": application.id, "channel": session.channel,
                "verified": session.verified, "reply": greeting}

    @app.post("/v1/sessions/{session_id}/turns")
    def turn(session_id: str, body: TurnIn):
        session = store.session(session_id)
        if session is None:
            raise HTTPException(404, "Unknown session")
        result = agent.handle(session, body.text, body.idempotency_key)
        # Metadata only: no message text in logs.
        log.info("turn session=%s intent=%s handoff=%s provider=%s", session_id, result["intent"],
                 result["handoff_reason"], result["nlu_provider"])
        return result

    @app.get("/v1/sessions/{session_id}")
    def session_detail(session_id: str):
        session = store.session(session_id)
        if session is None:
            raise HTTPException(404, "Unknown session")
        return {"session_id": session.id, "channel": session.channel, "verified": session.verified,
                "consent": session.consent, "handed_off": session.handed_off,
                "turns": store.turns(session_id), "actions": store.actions(session_id)}

    @app.post("/v1/webhooks/application-events")
    async def application_event(request: Request, x_signature: str = Header(default="")):
        if not settings.webhook_secret:
            raise HTTPException(503, "Webhook disabled: WEBHOOK_SECRET is not set")
        raw = await request.body()
        expected = "sha256=" + hmac.new(settings.webhook_secret.encode(), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, x_signature):
            raise HTTPException(401, "Invalid signature")
        try:
            event = EventIn.model_validate(json.loads(raw))
        except ValueError as exc:
            raise HTTPException(422, f"Invalid event: {exc}") from exc
        if event.stage not in STAGES:
            raise HTTPException(422, f"Unknown stage: {event.stage}")
        if store.application(event.application_id) is None:
            raise HTTPException(404, "Unknown application")
        if not store.mark_event(event.event_id, clock().isoformat()):
            return {"status": "duplicate", "event_id": event.event_id}
        store.set_stage(event.application_id, event.stage)
        return {"status": "applied", "event_id": event.event_id, "stage": event.stage}

    @app.get("/metrics")
    def get_metrics():
        return metrics.summary()

    return app


app = create_app()

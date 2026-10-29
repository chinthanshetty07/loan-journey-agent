from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.config import IST, Settings
from app.main import create_app

NOW = datetime(2026, 10, 29, 15, 0, tzinfo=IST)  # a Thursday afternoon


@pytest.fixture
def client():
    return TestClient(create_app(Settings(webhook_secret="test-secret"), clock=lambda: NOW))


@pytest.fixture
def talk(client):
    """Open a session and return a function that sends one message and returns the turn result."""

    def start(customer="CUST-01", channel="in_app", verified=True):
        sid = client.post("/v1/sessions", json={"customer_id": customer, "channel": channel, "verified": verified}).json()["session_id"]

        def say(text, key=None):
            body = {"text": text} if key is None else {"text": text, "idempotency_key": key}
            res = client.post(f"/v1/sessions/{sid}/turns", json=body)
            assert res.status_code == 200, res.text
            return res.json()

        say.sid = sid
        return say

    return start

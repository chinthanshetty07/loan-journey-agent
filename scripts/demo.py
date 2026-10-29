"""Prints a few example conversations against the in-process API. Run: python -m scripts.demo"""
from datetime import datetime

from fastapi.testclient import TestClient

from app.config import IST, Settings
from app.main import create_app

client = TestClient(create_app(Settings(), clock=lambda: datetime(2026, 10, 29, 15, 0, tzinfo=IST)))
SCRIPTS = [
    ("CUST-01", "in_app", True, ["loan ka kya hua", "haan"]),
    ("CUST-03", "voice", True, ["emi for 5 lakh over 3 years"]),
    ("CUST-02", "voice", False, ["which documents are pending"]),
    ("CUST-04", "whatsapp", True, ["call me back", "9pm", "ok"]),
    ("CUST-05", "in_app", True, ["any update on my application?"]),
]
for customer, channel, verified, lines in SCRIPTS:
    s = client.post("/v1/sessions", json={"customer_id": customer, "channel": channel, "verified": verified}).json()
    print(f"\n--- {customer} on {channel} ({'verified' if verified else 'not verified'}) ---")
    for text in lines:
        r = client.post(f"/v1/sessions/{s['session_id']}/turns", json={"text": text}).json()
        print(f"customer: {text}\nagent:    {r['reply']}")
        if r["tts_text"]:
            print(f"tts:      {r['tts_text']}")
        extras = [a["type"] for a in r["actions"]]
        if extras or r["handoff_reason"]:
            print(f"          actions={extras} handoff={r['handoff_reason']}")

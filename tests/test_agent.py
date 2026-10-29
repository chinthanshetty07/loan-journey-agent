import hashlib
import hmac
import json

from conftest import NOW


def test_unknown_customer_is_404(client):
    assert client.post("/v1/sessions", json={"customer_id": "NOPE", "channel": "in_app"}).status_code == 404


def test_kyc_offer_then_resend(talk):
    say = talk("CUST-01")
    r = say("what's the status of my loan")
    assert "video KYC" in r["reply"] and r["actions"] == []
    r = say("yes please")
    assert r["intent"] == "kyc_help" and r["actions"][0]["type"] == "kyc_link_sent"


def test_kyc_resend_limit_hands_off(talk):
    say = talk("CUST-01")
    for _ in range(3):
        assert say("resend my kyc link")["actions"][0]["type"] == "kyc_link_sent"
    r = say("resend my kyc link")
    assert r["handoff_reason"] == "kyc_resend_limit"


def test_unverified_voice_caller_gets_no_details(talk):
    say = talk("CUST-02", channel="voice", verified=False)
    for text in ["what's my application status", "what is my emi", "which documents are pending"]:
        r = say(text)
        assert r["actions"] == [{"type": "verification_required"}]
        assert "bank statement" not in r["reply"] and "₹" not in r["reply"]


def test_verified_voice_gets_speakable_text(talk):
    say = talk("CUST-03", channel="voice", verified=True)
    r = say("emi for 5 lakh over 3 years")
    assert "₹5,00,000" in r["reply"] and "5 lakh rupees" in r["tts_text"] and "₹" not in r["tts_text"]
    assert r["actions"][0] == {"type": "emi_quote", "amount_inr": 500000, "tenure_months": 36,
                               "rate_annual_pct": 13.0, "emi_inr": 16847}


def test_in_app_has_no_tts(talk):
    assert talk("CUST-03")("hi")["tts_text"] is None


def test_documents_listed_for_pending_stage(talk):
    r = talk("CUST-02")("which documents do you need")
    assert "bank statement and latest salary slip" in r["reply"]


def test_declined_application_goes_to_a_person(talk):
    r = talk("CUST-05")("any update on my application?")
    assert r["handoff_reason"] == "decline_explanation"


def test_two_misunderstandings_hand_off_and_stay_handed_off(talk):
    say = talk("CUST-03")
    assert say("purple monkey")["handoff_reason"] is None
    assert say("dishwasher")["handoff_reason"] == "not_understood"
    r = say("what's my status")
    assert r["intent"] == "handed_off" and "executive" in r["reply"]


def test_angry_or_complaint_or_human_request_hands_off(talk):
    assert talk("CUST-03")("this is the worst, useless app")["handoff_reason"] == "angry_customer"
    assert talk("CUST-03")("I want to file a complaint")["handoff_reason"] == "complaint"
    assert talk("CUST-03")("let me talk to a real person")["handoff_reason"] == "customer_request"


def test_callback_inside_window_books(talk):
    r = talk("CUST-04")("call me back at 5 pm")
    assert r["actions"] == [{"type": "callback_scheduled", "at": NOW.replace(hour=17).isoformat()}]


def test_callback_outside_window_proposes_then_confirms(talk):
    say = talk("CUST-04")
    assert "What time works" in say("can you call me later")["reply"]
    r = say("9pm")
    assert "Shall I book tomorrow at 9 am" in r["reply"] and r["actions"] == []
    r = say("ok")
    assert r["actions"][0] == {"type": "callback_scheduled", "at": "2026-10-30T09:00:00+05:30"}


def test_opt_out_then_explicit_callback_request_is_audited(client, talk):
    say = talk("CUST-04")
    assert say("please stop calling me")["actions"] == [{"type": "consent_revoked"}]
    r = say("actually call me tomorrow at 11 am")
    assert [a["type"] for a in r["actions"]] == ["consent_granted", "callback_scheduled"]
    detail = client.get(f"/v1/sessions/{say.sid}").json()
    assert detail["consent"] is True and [a["type"] for a in detail["actions"]] == ["consent_revoked", "consent_granted", "callback_scheduled"]


def test_idempotent_retry_does_not_resend(client, talk):
    say = talk("CUST-01")
    first = say("send kyc link", key="k-1")
    again = say("send kyc link", key="k-1")
    assert again["replayed"] is True and again["reply"] == first["reply"]
    actions = client.get(f"/v1/sessions/{say.sid}").json()["actions"]
    assert [a["type"] for a in actions] == ["kyc_link_sent"]


def test_raw_pii_is_never_stored(client, talk):
    say = talk("CUST-03")
    say("my pan is ABCDE1234F and phone 9876543210, what's the status")
    stored = json.dumps(client.get(f"/v1/sessions/{say.sid}").json())
    assert "ABCDE1234F" not in stored and "9876543210" not in stored and "[PAN]" in stored


def _signed(client, body: dict, secret="test-secret"):
    raw = json.dumps(body).encode()
    sig = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return client.post("/v1/webhooks/application-events", content=raw,
                       headers={"X-Signature": sig, "Content-Type": "application/json"})


def test_webhook_updates_stage_and_dedupes(client, talk):
    event = {"event_id": "evt-1", "application_id": "APP-1003", "stage": "approved"}
    assert _signed(client, event).json()["status"] == "applied"
    assert _signed(client, event).json()["status"] == "duplicate"
    assert "approved" in talk("CUST-03")("status?")["reply"]


def test_webhook_rejects_bad_signature_and_bad_stage(client):
    assert _signed(client, {"event_id": "e", "application_id": "APP-1003", "stage": "approved"}, secret="wrong").status_code == 401
    assert _signed(client, {"event_id": "e2", "application_id": "APP-1003", "stage": "teleported"}).status_code == 422
    assert _signed(client, {"event_id": "e3", "application_id": "APP-9999", "stage": "approved"}).status_code == 404


def test_webhook_disabled_without_secret():
    from fastapi.testclient import TestClient
    from app.config import Settings
    from app.main import create_app
    c = TestClient(create_app(Settings(webhook_secret="")))
    assert c.post("/v1/webhooks/application-events", content=b"{}").status_code == 503


def test_metrics_count_turns_and_handoffs(client, talk):
    say = talk("CUST-05")
    say("hi")
    say("status")
    m = client.get("/metrics").json()
    assert m["turns"] == 2 and m["handoffs_by_reason"] == {"decline_explanation": 1} and m["handoff_rate"] == 0.5

# Loan Journey Agent

[![CI](https://github.com/chinthanshetty07/loan-journey-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/chinthanshetty07/loan-journey-agent/actions/workflows/ci.yml)

A FastAPI backend for an AI assistant that lives inside a lender's app, voice line or WhatsApp
and helps customers through a loan application: where it stands, the KYC link, pending
documents, an EMI estimate, or a callback at a time that suits them. It hands the conversation
to a person when it should.

Built as an independent prototype after reading about Revrag AI's in-app and voice agents for
BFSI. It is not affiliated with Revrag, and **all customer data is synthetic** (`data/applications.json`).

## Design choice: the model understands, code acts

In lending, a wrong number or an unauthorised disclosure is worse than a clumsy reply. So:

- The language model (optional) only **classifies** each message: intent, sentiment and confidence,
  via Claude structured outputs. It never writes customer-facing text.
- **Amounts, tenures and callback times are parsed by code**, and replies are templates filled
  from the application record. The agent never acts on a number a model produced.
- If the model refuses, returns bad output or the API fails, the turn falls back to the
  rule-based classifier: a model outage degrades quality, not availability.

```
message -> redact PII -> classify (rules | Claude) -> policy -> deterministic tool -> compliance gate -> reply (+ TTS text)
```

## What it does

| Area | Behaviour |
|---|---|
| Status | Stage-aware replies (KYC pending, documents pending, under review, approved, disbursed). A declined application always goes to a person. |
| KYC | Offers and resends the video-KYC link; a limit of 3 resends a day, then a handoff. |
| EMI | Reducing-balance EMI from the customer's amount and tenure ("3 lakh over 2 years") or the application's own terms. |
| Callbacks | Books inside a contact window (default 9 am–7 pm IST). Outside it, proposes the next slot and books on "yes". Multi-turn: "call me back" → "9pm" → "ok". |
| Consent | "Stop calling me" revokes contact consent; an explicit later request for a call restores it, and both are audited. |
| Verification gate | On voice, a caller the IVR hasn't verified gets no application details, only a prompt to verify by OTP. |
| Handoffs | Requests for a person, complaints, anger, two misunderstood messages in a row, KYC resend limit, declined applications, compliance blocks. After a handoff the bot stays out of the way. |
| Privacy | Aadhaar, PAN, phone, email and account numbers are redacted before anything is stored or sent to a model. Logs hold metadata only. |
| Voice | `tts_text` for voice sessions: "₹16,847" becomes "16 thousand 847 rupees", "EMI" becomes "E M I". |
| Reliability | Idempotency keys replay a retried turn instead of resending a link twice. Lender webhooks are HMAC-signed and de-duplicated by event id. |
| Observability | `/metrics`: turns, p50/p95 latency, handoff rate by reason, intents, NLU provider, LLM tokens and cost. |

A few common Hinglish phrasings ("loan ka kya hua", "call mat karo") are recognised by the rules.
That is not real multilingual support; the Claude mode is the path to that.

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/sessions` | Start a session: `{customer_id, channel: in_app\|voice\|whatsapp, verified}` |
| POST | `/v1/sessions/{id}/turns` | Send a message: `{text, idempotency_key?}` → reply, `tts_text`, actions, handoff |
| GET | `/v1/sessions/{id}` | Redacted transcript, consent and audit trail of actions |
| POST | `/v1/webhooks/application-events` | Lender stage updates, header `X-Signature: sha256=<hmac>` |
| GET | `/metrics`, `/health` | Metrics and health |

Example run (`python -m scripts.demo`, full output in [docs/demo-output.txt](docs/demo-output.txt)):

```
customer: call me back
agent:    Sure. What time works for you? We can call between 9 am and 7 pm.
customer: 9pm
agent:    We only call between 9 am and 7 pm. Shall I book tomorrow at 9 am instead?
customer: ok
agent:    Booked. We'll call you tomorrow at 9 am.
```

## Run it

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/uvicorn app.main:app --reload        # http://localhost:8000/docs
.venv/bin/pytest -q                            # 43 tests
.venv/bin/python -m eval.run_eval              # offline eval with quality gates
docker compose up --build                      # containerised, SQLite on a volume
```

Optional settings: `NLU_PROVIDER=claude` with `ANTHROPIC_API_KEY` set in your environment,
`WEBHOOK_SECRET` to enable the webhook, `CONTACT_START_HOUR` / `CONTACT_END_HOUR`, `DB_PATH`.

## Tests and eval

- **43 pytest tests**: parsing (Indian amounts, tenures, times), redaction, the EMI formula, every
  policy path above, idempotent replay, webhook signatures and de-duplication, and the Claude
  classifier against a mocked client (structured-output request, cost, refusal, bad output, API error).
- **Offline eval** (`eval/cases.jsonl`, 38 single-message cases in English and Hinglish), run in CI
  with gates. Current result with the rule-based classifier:

| Metric | Score | Gate |
|---|---|---|
| Intent accuracy | 0.969 (31/32) | ≥ 0.85 |
| Action accuracy | 1.0 (16/16) | ≥ 0.90 |
| Handoff accuracy | 1.0 (38/38) | ≥ 0.95 |
| Safety (no disclosure to unverified callers, no raw PII stored) | 1.0 (5/5) | = 1.0 |

The one miss is real and left in: "has my loan been approved yet" is not recognised as a status
question. Keyword rules are brittle in exactly this way, which is why the classifier is pluggable.

## Limitations

- The Claude classifier is tested only against a mocked client, not run live.
- The eval is small, single-turn and written by me; it is a regression gate, not a quality claim.
- No real STT/TTS, telephony, OTP or SMS integration: verification comes in as a flag, and sent
  links are recorded as actions.
- SQLite and in-process metrics keep the prototype self-contained; production would use Postgres,
  a queue for outbound actions and exported metrics.
- The contact window and banned-phrase list are configurable placeholders, not a statement of
  any regulator's rules.

Stack: Python 3.12, FastAPI, Pydantic, SQLite, Anthropic SDK (optional), pytest, Docker, GitHub Actions.

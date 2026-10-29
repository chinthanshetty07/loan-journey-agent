"""Offline eval with quality gates. Exits non-zero if any gate fails, so CI blocks the change.

Each case is one customer message in a fresh session. Intent accuracy is scored only on cases
that name an intent; action and handoff checks on cases that name one. Safety cases (no details
for unverified voice callers, no raw PII stored) must all pass.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import IST, Settings
from app.main import create_app

CASES = Path(__file__).with_name("cases.jsonl")
GATES = {"intent_accuracy": 0.85, "action_accuracy": 0.90, "handoff_accuracy": 0.95, "safety": 1.0}
NOW = datetime(2026, 10, 29, 15, 0, tzinfo=IST)


def run() -> dict:
    client = TestClient(create_app(Settings(), clock=lambda: NOW))
    tally = {k: [0, 0] for k in GATES}
    failures = []
    for line in CASES.read_text().splitlines():
        case = json.loads(line)
        sid = client.post("/v1/sessions", json={"customer_id": case["customer"], "channel": case.get("channel", "in_app"),
                                                 "verified": case.get("verified", True)}).json()["session_id"]
        r = client.post(f"/v1/sessions/{sid}/turns", json={"text": case["text"]}).json()
        types = [a["type"] for a in r["actions"]]
        checks = []
        if "intent" in case:
            checks.append(("intent_accuracy", r["intent"] == case["intent"], f"intent {r['intent']} != {case['intent']}"))
        if "action" in case:
            checks.append(("action_accuracy", case["action"] in types, f"missing action {case['action']} (got {types})"))
        if "no_action" in case:
            checks.append(("action_accuracy", case["no_action"] not in types, f"unexpected action {case['no_action']}"))
        expected_handoff = case.get("handoff")
        checks.append(("handoff_accuracy", r["handoff_reason"] == expected_handoff,
                       f"handoff {r['handoff_reason']} != {expected_handoff}"))
        if case.get("safety"):
            ok = True
            if case.get("action") == "verification_required":
                ok = types == ["verification_required"] and "₹" not in r["reply"]
            if "pii" in case:
                stored = json.dumps(client.get(f"/v1/sessions/{sid}").json())
                ok = ok and case["pii"] not in stored
            checks.append(("safety", ok, "safety check failed"))
        for metric, passed, why in checks:
            tally[metric][1] += 1
            tally[metric][0] += int(passed)
            if not passed:
                failures.append(f"{case['id']} ({case['text']!r}): {why}")
    scores = {k: round(p / n, 3) if n else 1.0 for k, (p, n) in tally.items()}
    return {"cases": len(CASES.read_text().splitlines()), "scores": scores, "counts": tally, "failures": failures}


if __name__ == "__main__":
    report = run()
    print(json.dumps(report, indent=2))
    failed = [k for k, floor in GATES.items() if report["scores"][k] < floor]
    if failed:
        print(f"FAILED gates: {failed}", file=sys.stderr)
        sys.exit(1)
    print("All gates passed.")

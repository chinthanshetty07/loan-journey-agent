import json
from types import SimpleNamespace

from app.nlu import ClaudeNLU


class FakeClient:
    def __init__(self, text='{"intent": "emi_quote", "confidence": 0.82, "sentiment": "neutral"}',
                 stop_reason="end_turn", error: Exception | None = None):
        self.calls = []
        self._text, self._stop, self._error = text, stop_reason, error
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error:
            raise self._error
        return SimpleNamespace(
            stop_reason=self._stop,
            content=[SimpleNamespace(type="text", text=self._text)],
            usage=SimpleNamespace(input_tokens=1000, output_tokens=50),
        )


def test_parses_structured_output_and_costs_it():
    client = FakeClient()
    intent = ClaudeNLU(client).parse("mahine ka kitna bharna padega", context="")
    assert (intent.name, intent.confidence, intent.provider) == ("emi_quote", 0.82, "claude")
    assert intent.usage == {"input_tokens": 1000, "output_tokens": 50, "cost_usd": 0.005}
    call = client.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert "mahine ka kitna" in call["messages"][0]["content"]


def test_refusal_falls_back_to_rules():
    intent = ClaudeNLU(FakeClient(stop_reason="refusal")).parse("what is my emi")
    assert intent.name == "emi_quote" and intent.provider == "rules (claude refusal)"


def test_unparseable_output_falls_back():
    intent = ClaudeNLU(FakeClient(text="not json")).parse("resend kyc link")
    assert intent.name == "kyc_help" and intent.provider == "rules (claude unparseable)"


def test_api_error_falls_back_without_failing_the_turn():
    intent = ClaudeNLU(FakeClient(error=TimeoutError("slow"))).parse("call me back")
    assert intent.name == "schedule_callback" and intent.provider == "rules (claude error:TimeoutError)"


def test_confidence_is_clamped():
    text = json.dumps({"intent": "greeting", "confidence": 7, "sentiment": "positive"})
    assert ClaudeNLU(FakeClient(text=text)).parse("hey").confidence == 1.0

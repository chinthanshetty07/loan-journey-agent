"""In-process metrics. A real deployment would export these to Prometheus or similar."""
from __future__ import annotations

import threading
from collections import Counter


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latencies: list[float] = []
        self._intents: Counter[str] = Counter()
        self._handoffs: Counter[str] = Counter()
        self._providers: Counter[str] = Counter()
        self._tokens = {"input": 0, "output": 0}
        self._cost = 0.0
        self.turns = 0

    def record(self, intent: str, latency_ms: float, handoff_reason: str | None, provider: str, usage: dict) -> None:
        with self._lock:
            self.turns += 1
            self._latencies.append(latency_ms)
            self._intents[intent] += 1
            self._providers[provider] += 1
            if handoff_reason:
                self._handoffs[handoff_reason] += 1
            self._tokens["input"] += usage.get("input_tokens", 0)
            self._tokens["output"] += usage.get("output_tokens", 0)
            self._cost += usage.get("cost_usd", 0.0)

    def summary(self) -> dict:
        with self._lock:
            lat = sorted(self._latencies)

            def pct(p: float) -> float:
                return round(lat[min(len(lat) - 1, int(p * len(lat)))], 2) if lat else 0.0

            return {
                "turns": self.turns,
                "latency_ms": {"p50": pct(0.50), "p95": pct(0.95)},
                "handoff_rate": round(sum(self._handoffs.values()) / self.turns, 3) if self.turns else 0.0,
                "handoffs_by_reason": dict(self._handoffs),
                "intents": dict(self._intents),
                "nlu_providers": dict(self._providers),
                "llm_tokens": dict(self._tokens),
                "llm_cost_usd": round(self._cost, 6),
            }

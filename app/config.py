"""Settings read from the environment. Nothing secret has a default."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30), "IST")


def now_ist() -> datetime:
    return datetime.now(IST)


@dataclass(frozen=True)
class Settings:
    nlu_provider: str = "rules"  # "rules" (offline, default) or "claude"
    webhook_secret: str = ""  # required for the webhook endpoint; unset means it is disabled
    db_path: str = ":memory:"
    contact_start_hour: int = 9  # callbacks are only booked inside this window (IST)
    contact_end_hour: int = 19
    min_confidence: float = 0.6  # below this the agent asks again; twice in a row it hands off
    max_kyc_resends_per_day: int = 3

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            nlu_provider=os.getenv("NLU_PROVIDER", "rules"),
            webhook_secret=os.getenv("WEBHOOK_SECRET", ""),
            db_path=os.getenv("DB_PATH", ":memory:"),
            contact_start_hour=int(os.getenv("CONTACT_START_HOUR", "9")),
            contact_end_hour=int(os.getenv("CONTACT_END_HOUR", "19")),
            min_confidence=float(os.getenv("MIN_CONFIDENCE", "0.6")),
        )

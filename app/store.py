"""SQLite storage. Only redacted text is ever written; raw customer messages are not stored."""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path

STAGES = [
    "started", "kyc_pending", "documents_pending", "under_review",
    "approved", "esign_pending", "disbursed", "rejected",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
  id TEXT PRIMARY KEY, customer_id TEXT UNIQUE NOT NULL, first_name TEXT NOT NULL,
  product TEXT NOT NULL, amount_inr INTEGER NOT NULL, rate_annual_pct REAL NOT NULL,
  tenure_months INTEGER NOT NULL, stage TEXT NOT NULL, pending_documents TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY, customer_id TEXT NOT NULL, application_id TEXT NOT NULL,
  channel TEXT NOT NULL, verified INTEGER NOT NULL, consent INTEGER NOT NULL DEFAULT 1,
  unknown_streak INTEGER NOT NULL DEFAULT 0, pending TEXT, proposed_slot TEXT,
  handed_off INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS turns (
  id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, idempotency_key TEXT,
  redacted_text TEXT NOT NULL, intent TEXT NOT NULL, response TEXT NOT NULL, created_at TEXT NOT NULL,
  UNIQUE (session_id, idempotency_key)
);
CREATE TABLE IF NOT EXISTS actions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, type TEXT NOT NULL,
  detail TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS processed_events (event_id TEXT PRIMARY KEY, received_at TEXT NOT NULL);
"""


@dataclass
class Application:
    id: str
    customer_id: str
    first_name: str
    product: str
    amount_inr: int
    rate_annual_pct: float
    tenure_months: int
    stage: str
    pending_documents: list[str]


@dataclass
class Session:
    id: str
    customer_id: str
    application_id: str
    channel: str
    verified: bool
    consent: bool
    unknown_streak: int
    pending: str | None
    proposed_slot: str | None
    handed_off: bool


class Store:
    def __init__(self, path: str = ":memory:") -> None:
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(SCHEMA)

    def seed(self, json_path: Path) -> None:
        rows = json.loads(Path(json_path).read_text())
        with self._lock, self._db:
            for r in rows:
                self._db.execute(
                    "INSERT OR IGNORE INTO applications VALUES (?,?,?,?,?,?,?,?,?)",
                    (r["id"], r["customer_id"], r["first_name"], r["product"], r["amount_inr"],
                     r["rate_annual_pct"], r["tenure_months"], r["stage"], json.dumps(r["pending_documents"])),
                )

    # applications
    def application_for_customer(self, customer_id: str) -> Application | None:
        row = self._one("SELECT * FROM applications WHERE customer_id = ?", (customer_id,))
        return self._app(row) if row else None

    def application(self, app_id: str) -> Application | None:
        row = self._one("SELECT * FROM applications WHERE id = ?", (app_id,))
        return self._app(row) if row else None

    def set_stage(self, app_id: str, stage: str) -> bool:
        with self._lock, self._db:
            return self._db.execute("UPDATE applications SET stage = ? WHERE id = ?", (stage, app_id)).rowcount == 1

    # sessions
    def create_session(self, customer_id: str, application_id: str, channel: str, verified: bool, now: str) -> Session:
        sid = "S-" + uuid.uuid4().hex[:12]
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO sessions (id, customer_id, application_id, channel, verified, created_at) VALUES (?,?,?,?,?,?)",
                (sid, customer_id, application_id, channel, int(verified), now),
            )
        return self.session(sid)

    def session(self, sid: str) -> Session | None:
        row = self._one("SELECT * FROM sessions WHERE id = ?", (sid,))
        if not row:
            return None
        return Session(row["id"], row["customer_id"], row["application_id"], row["channel"], bool(row["verified"]),
                       bool(row["consent"]), row["unknown_streak"], row["pending"], row["proposed_slot"],
                       bool(row["handed_off"]))

    def save_session(self, s: Session) -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE sessions SET consent=?, unknown_streak=?, pending=?, proposed_slot=?, handed_off=? WHERE id=?",
                (int(s.consent), s.unknown_streak, s.pending, s.proposed_slot, int(s.handed_off), s.id),
            )

    # turns, with idempotent replay
    def find_turn(self, sid: str, key: str) -> dict | None:
        row = self._one("SELECT response FROM turns WHERE session_id = ? AND idempotency_key = ?", (sid, key))
        return json.loads(row["response"]) if row else None

    def record_turn(self, sid: str, key: str | None, redacted_text: str, intent: str, response: dict, now: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO turns (session_id, idempotency_key, redacted_text, intent, response, created_at) VALUES (?,?,?,?,?,?)",
                (sid, key, redacted_text, intent, json.dumps(response), now),
            )

    def turns(self, sid: str) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT redacted_text, intent, created_at FROM turns WHERE session_id = ? ORDER BY id", (sid,)).fetchall()
        return [dict(r) for r in rows]

    # actions (KYC link resends, callbacks, handoffs)
    def record_action(self, sid: str, type_: str, detail: dict, now: str) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT INTO actions (session_id, type, detail, created_at) VALUES (?,?,?,?)",
                             (sid, type_, json.dumps(detail), now))

    def count_actions_since(self, application_id: str, type_: str, since: str) -> int:
        row = self._one(
            "SELECT COUNT(*) AS n FROM actions a JOIN sessions s ON s.id = a.session_id "
            "WHERE s.application_id = ? AND a.type = ? AND a.created_at >= ?",
            (application_id, type_, since),
        )
        return row["n"]

    def actions(self, sid: str) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT type, detail, created_at FROM actions WHERE session_id = ? ORDER BY id", (sid,)).fetchall()
        return [{"type": r["type"], "detail": json.loads(r["detail"]), "created_at": r["created_at"]} for r in rows]

    # webhook de-duplication
    def mark_event(self, event_id: str, now: str) -> bool:
        """True the first time an event id is seen, False on a retry."""
        try:
            with self._lock, self._db:
                self._db.execute("INSERT INTO processed_events VALUES (?, ?)", (event_id, now))
            return True
        except sqlite3.IntegrityError:
            return False

    def _one(self, sql: str, params: tuple):
        with self._lock:
            return self._db.execute(sql, params).fetchone()

    @staticmethod
    def _app(row) -> Application:
        return Application(row["id"], row["customer_id"], row["first_name"], row["product"], row["amount_inr"],
                           row["rate_annual_pct"], row["tenure_months"], row["stage"], json.loads(row["pending_documents"]))

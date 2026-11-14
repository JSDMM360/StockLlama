"""
PostgreSQL storage for the event log.

One row per user input in the table  event_logs :

    id                SERIAL   unique event id (shown in the Event Logs tab)
    event_uid         client generated id used to attach later events
    logged_at         date and time of the input
    source            typed | voice | file | manual
    user_input        the input text / payload
    model_output      the model's raw response(s)
    model_name        model identifier (e.g. tinyllama:latest)
    inference_ms      total inference time in milliseconds
    prompt_tokens     tokens consumed by the prompt(s)
    completion_tokens tokens produced by the model
    meta              JSONB extras
    events            JSONB timeline of everything that happened as a result

It reuses DB_CONFIG from config.py (same database as the inventory tables) but
keeps its own connection, so logging can never interfere with inventory
transactions.  Every method raises on a database error; ActivityLogger turns
that into a local-file fallback.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from typing import Optional

import psycopg2
from psycopg2.extras import Json, RealDictCursor

from config import DB_CONFIG

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS event_logs (
    id                SERIAL PRIMARY KEY,
    event_uid         VARCHAR(32) NOT NULL UNIQUE,
    logged_at         TIMESTAMP   NOT NULL DEFAULT NOW(),
    source            VARCHAR(20) NOT NULL DEFAULT 'typed',
    user_input        TEXT        NOT NULL,
    model_output      TEXT,
    model_name        VARCHAR(120),
    inference_ms      INTEGER,
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    meta              JSONB       NOT NULL DEFAULT '{}'::jsonb,
    events            JSONB       NOT NULL DEFAULT '[]'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_event_logs_logged_at ON event_logs (logged_at DESC);
"""

MAX_OUTPUT_CHARS = 200_000


def _json(obj):
    return Json(obj, dumps=lambda o: json.dumps(o, ensure_ascii=False, default=str))


def _ts(value) -> datetime:
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return datetime.now()


class EventStore:
    def __init__(self, config: Optional[dict] = None, connect_timeout: int = 3):
        self.config = dict(config or DB_CONFIG)
        self.config.setdefault("connect_timeout", connect_timeout)
        self._conn = None
        self._lock = threading.RLock()

    # ── connection ────────────────────────────────────────────────────────
    def ensure(self):
        """Connect (and create the table) if needed. Raises on failure."""
        with self._lock:
            if self._conn is not None and not self._conn.closed:
                return
            conn = psycopg2.connect(**self.config)
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(SCHEMA_SQL)
            self._conn = conn

    def _drop(self):
        with self._lock:
            try:
                if self._conn is not None:
                    self._conn.close()
            except Exception:
                pass
            self._conn = None

    def close(self):
        self._drop()

    # ── writing ───────────────────────────────────────────────────────────
    def _apply_cur(self, cur, op: dict):
        kind = op["op"]
        if kind == "begin":
            cur.execute(
                "INSERT INTO event_logs (event_uid, logged_at, source, user_input, meta) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (event_uid) DO NOTHING",
                (op["uid"], _ts(op["ts"]), op.get("source", "typed"),
                 op.get("prompt", ""), _json(op.get("meta") or {})))
        elif kind == "event":
            entry = {"ts": op["ts"], "kind": op["kind"], "message": op["message"],
                     "data": op.get("data") or {}}
            cur.execute("UPDATE event_logs SET events = events || %s WHERE event_uid = %s",
                        (_json([entry]), op["uid"]))
        elif kind == "metrics":
            out = op.get("output")
            if out is not None:
                out = str(out)[:MAX_OUTPUT_CHARS]
            cur.execute(
                """UPDATE event_logs SET
                     model_name   = COALESCE(%(model)s::text, model_name),
                     model_output = CASE WHEN %(out)s::text IS NULL THEN model_output
                                         WHEN model_output IS NULL THEN %(out)s::text
                                         ELSE model_output || E'\\n' || %(out)s::text END,
                     inference_ms      = COALESCE(inference_ms, 0)      + COALESCE(%(ms)s::int, 0),
                     prompt_tokens     = COALESCE(prompt_tokens, 0)     + COALESCE(%(pt)s::int, 0),
                     completion_tokens = COALESCE(completion_tokens, 0) + COALESCE(%(ct)s::int, 0)
                   WHERE event_uid = %(uid)s""",
                {"model": op.get("model"), "out": out, "ms": op.get("ms"),
                 "pt": op.get("pt"), "ct": op.get("ct"), "uid": op["uid"]})
        else:
            raise ValueError(f"unknown log operation: {kind}")

    def apply(self, op: dict):
        """Apply one operation. Raises psycopg2.Error / OSError when the DB is down."""
        with self._lock:
            self.ensure()
            try:
                with self._conn.cursor() as cur:
                    self._apply_cur(cur, op)
            except psycopg2.Error:
                self._drop()
                raise

    def apply_many(self, ops: list):
        """Apply several operations in ONE transaction (all or nothing)."""
        with self._lock:
            self.ensure()
            conn = self._conn
            try:
                conn.autocommit = False
                with conn.cursor() as cur:
                    for op in ops:
                        self._apply_cur(cur, op)
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                self._drop()
                raise
            finally:
                try:
                    conn.autocommit = True
                except Exception:
                    pass

    # ── reading ───────────────────────────────────────────────────────────
    def read(self, limit: int = 500) -> list:
        """Newest-first list of entry dicts (events included)."""
        with self._lock:
            self.ensure()
            try:
                with self._conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        "SELECT id, event_uid, logged_at, source, user_input, model_output, "
                        "model_name, inference_ms, prompt_tokens, completion_tokens, meta, "
                        "events FROM event_logs ORDER BY logged_at DESC, id DESC LIMIT %s",
                        (limit,))
                    rows = cur.fetchall()
            except psycopg2.Error:
                self._drop()
                raise
        entries = []
        for r in rows:
            pt, ct = r["prompt_tokens"], r["completion_tokens"]
            entries.append({
                "id": r["event_uid"], "event_id": r["id"],
                "timestamp": r["logged_at"].isoformat(timespec="seconds"),
                "source": r["source"], "prompt": r["user_input"],
                "output": r["model_output"], "model": r["model_name"],
                "inference_ms": r["inference_ms"], "prompt_tokens": pt,
                "completion_tokens": ct,
                "total_tokens": None if pt is None and ct is None else (pt or 0) + (ct or 0),
                "meta": r["meta"] or {}, "events": list(r["events"] or []),
            })
        return entries
